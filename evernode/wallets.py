"""Wallet-profile state and root-only seed recovery helpers."""

import json
import os
import re

from . import configuration, process, scripts, storage

ADDRESS_PATTERN = re.compile(r"^-?[0-9]+:[0-9a-fA-F]{64}$")


def validate_wallet_profile(profile, *, validator_type="depool"):
    if not isinstance(profile, dict) or set(profile) != {
        "mode",
        "custodians",
        "required_signatures",
        "wallet_address",
        "depool_address",
    }:
        raise process.OperationError("Invalid wallet profile")
    if profile["mode"] not in ("new", "import"):
        raise process.OperationError("Invalid wallet mode")
    if type(profile["custodians"]) is not int or not 1 <= profile["custodians"] <= 31:
        raise process.OperationError("Custodian count must be between 1 and 31")
    if (
        type(profile["required_signatures"]) is not int
        or not 1 <= profile["required_signatures"] <= profile["custodians"]
    ):
        raise process.OperationError(
            "Required signatures must be between 1 and the custodian count"
        )
    for key in ("wallet_address", "depool_address"):
        value = profile[key]
        if value is not None and (
            not isinstance(value, str) or not ADDRESS_PATTERN.fullmatch(value)
        ):
            raise process.OperationError(f"Invalid {key.replace('_', ' ')}")
    # Neither a solo nor a nominator validator has a DePool, so an import of
    # one is complete with just its Safe; requiring a DePool address would
    # make those modes unreachable, and accepting one would describe a
    # contract they never use. A nominator's own contract is not a DePool and
    # is derived rather than supplied.
    if validator_type != "depool" and profile["depool_address"] is not None:
        raise process.OperationError(
            f"A {validator_type} validator has no DePool address"
        )
    if profile["mode"] == "import":
        if profile["wallet_address"] is None:
            raise process.OperationError(
                "An imported validator requires its existing wallet address"
            )
        if validator_type == "depool" and profile["depool_address"] is None:
            raise process.OperationError(
                "An imported validator requires expected wallet and DePool addresses"
            )
    return profile


def node_wallet_profile(config):
    """Validate a saved profile against what this node's type requires."""
    return validate_wallet_profile(
        config["wallet"], validator_type=configuration.validator_type(config)
    )


def seed_directory(store, config):
    return store.node_directory(config["name"]) / "keys" / ("MSKeys_" + config["name"])


def ensure_unassigned(store, config, wallet_address=None, depool_address=None):
    """Reject accidentally attaching one wallet/DePool to two local nodes."""
    wanted = {value.lower() for value in (wallet_address, depool_address) if value}
    if not wanted:
        return
    for name in store.list_node_names():
        if name == config["name"]:
            continue
        profile = store.load_node_config(name).get("wallet")
        if not isinstance(profile, dict):
            continue
        assigned = {
            value.lower()
            for value in (profile.get("wallet_address"), profile.get("depool_address"))
            if isinstance(value, str)
        }
        if wanted & assigned:
            raise process.OperationError(
                "Wallet or DePool address is already assigned to managed node " + name
            )


def ensure_owner_is_external(store, config, owner):
    """Refuse an owner wallet that this server already controls.

    The point of nominator staking is that the key able to withdraw the
    principal is not on the validator. An owner address that is one of this
    host's managed wallets would silently give that back.
    """
    wanted = str(owner).lower()
    for name in store.list_node_names():
        saved = store.load_node_config(name)
        profile = saved.get("wallet")
        if not isinstance(profile, dict):
            continue
        for key in ("wallet_address", "depool_address"):
            value = profile.get(key)
            if value and value.lower() == wanted:
                raise process.OperationError(
                    f"{owner} is managed by node {name} on this server, so "
                    "its key is here too. A nominator owner has to be a cold "
                    "wallet this host does not hold."
                )
        held = (saved.get("settings") or {}).get("NOMINATOR_ADDR")
        if held and held.lower() == wanted:
            raise process.OperationError(
                f"{owner} is node {name}'s nominator contract, not a wallet "
                "that can own one"
            )


def store_import_seeds(store, config, phrases):
    """Write phrases only to the files read by Msig-prep.sh, mode 0600."""
    profile = node_wallet_profile(config)
    if len(phrases) != profile["custodians"]:
        raise process.OperationError("Seed phrase count does not match custodian count")
    directory = seed_directory(store, config)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    storage.validate_private_path(directory)
    for index, phrase in enumerate(phrases, 1):
        if (
            not isinstance(phrase, str)
            or "\n" in phrase
            or "\r" in phrase
            or len(phrase.split()) < 12
        ):
            raise process.OperationError(
                "Each seed phrase must be a single mnemonic phrase"
            )
        target = directory / f"{config['name']}_seed_{index}.txt"
        if target.exists():
            raise process.OperationError(
                "Seed files already exist; refusing to replace an imported wallet"
            )
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(phrase.strip() + "\n")
            output.flush()
            os.fsync(output.fileno())
        storage.validate_private_path(target)


def _public_key_summary(directory, name):
    values = []
    for path in sorted(directory.glob(name + "_*.keys.json")):
        storage.validate_private_path(path)
        try:
            content = json.loads(path.read_text())
        except ValueError:
            continue
        if isinstance(content, dict):
            public = content.get("public") or content.get("public_key")
            if isinstance(public, str):
                values.append(public)
    return values


def _check_new_wallet_files(keys, name, custodians):
    """Catch stale root files before presenting an address to fund."""
    generated = keys / f"MSKeys_{name}"
    for path in (generated, generated / f"{name}.addr"):
        if not path.exists():
            raise process.OperationError(f"Missing prepared wallet file: {path.name}")
        storage.validate_private_path(path)
    root_address = (keys / f"{name}.addr").read_text().strip()
    generated_address = (generated / f"{name}.addr").read_text().strip()
    if root_address.lower() != generated_address.lower():
        raise process.OperationError(
            "Prepared wallet address differs from the root address file"
        )
    for index in range(1, custodians + 1):
        filename = f"{name}_{index}.keys.json"
        root_key = keys / filename
        generated_key = generated / filename
        for path in (root_key, generated_key):
            if not path.is_file():
                raise process.OperationError(f"Missing prepared wallet key {index}")
            storage.validate_private_path(path)
        try:
            same_key = json.loads(root_key.read_text()) == json.loads(
                generated_key.read_text()
            )
        except ValueError as exc:
            raise process.OperationError(
                f"Invalid prepared wallet key {index}"
            ) from exc
        if not same_key:
            raise process.OperationError(
                f"Prepared wallet key {index} differs from the root key file"
            )


def prepare_wallet(store, config, *, imported=False):
    """Use the reference generator and return only non-secret wallet facts."""
    profile = node_wallet_profile(config)
    if imported:
        directory = seed_directory(store, config)
        if not directory.is_dir():
            raise process.OperationError("Missing imported seed file 1")
        storage.validate_private_path(directory)
        for index in range(1, profile["custodians"] + 1):
            seed = directory / f"{config['name']}_seed_{index}.txt"
            if not seed.is_file():
                raise process.OperationError(f"Missing imported seed file {index}")
            storage.validate_private_path(seed)
    scripts.execute_validator_script(
        store,
        config,
        "Msig-prep.sh",
        "--",
        "Safe",
        str(profile["custodians"]),
        str(wallet_workchain(config)),
        timeout=300,
        sensitive=True,
    )
    keys = store.node_directory(config["name"]) / "keys"
    address_file = keys / (config["name"] + ".addr")
    storage.validate_private_path(address_file)
    address = address_file.read_text().strip()
    if not ADDRESS_PATTERN.fullmatch(address):
        raise process.OperationError(
            "Msig-prep.sh did not create a valid wallet address"
        )
    if not imported:
        _check_new_wallet_files(keys, config["name"], profile["custodians"])
    if (
        not imported
        and profile["wallet_address"]
        and address.lower() != profile["wallet_address"].lower()
    ):
        raise process.OperationError(
            "Recovered wallet address does not match the supplied address"
        )
    if imported:
        address = profile["wallet_address"]
        key_directory = seed_directory(store, config)
    else:
        key_directory = keys
    ensure_unassigned(store, config, address, profile["depool_address"])
    return {
        "address": address,
        "public_keys": _public_key_summary(key_directory, config["name"]),
        "key_directory": str(key_directory),
    }


def _check_imported_wallet(profile, local_public_keys, observed):
    if (observed["custodians"], observed["required_signatures"]) != (
        profile["custodians"],
        profile["required_signatures"],
    ):
        raise process.OperationError(
            "Imported wallet custodians or signature threshold do not match the supplied profile"
        )
    try:
        local = {int(key.removeprefix("0x"), 16) for key in local_public_keys}
        remote = {int(key.removeprefix("0x"), 16) for key in observed["public_keys"]}
    except (KeyError, TypeError, ValueError) as exc:
        raise process.OperationError(
            "Could not compare imported custodian keys"
        ) from exc
    if len(local) != profile["custodians"] or local != remote:
        raise process.OperationError(
            "Imported seed public keys do not match the on-chain wallet custodians"
        )


def _install_imported_wallet_files(store, config, address):
    """Replace provisional script outputs only after on-chain key verification."""
    name = config["name"]
    keys = store.node_directory(name) / "keys"
    source = seed_directory(store, config)
    storage.validate_private_path(keys)
    storage.validate_private_path(source)
    for index in range(1, config["wallet"]["custodians"] + 1):
        path = source / f"{name}_{index}.keys.json"
        storage.validate_private_path(path)
        try:
            keypair = json.loads(path.read_text())
            if not isinstance(keypair, dict) or not all(
                isinstance(keypair.get(field), str) for field in ("public", "secret")
            ):
                raise ValueError("Invalid keypair")
        except ValueError as exc:
            raise process.OperationError(f"Invalid imported keypair {index}") from exc
        storage.write_json_atomically(keys / path.name, keypair)
    storage.write_private_text_atomically(keys / f"{name}.addr", address + "\n")


def wallet_workchain(config):
    """The workchain this node's Safe belongs in.

    A solo validator stakes straight to the elector, which accepts a stake
    only from the masterchain, so its Safe has to live there. A nominator
    validator's Safe instructs a masterchain contract, which refuses any
    other workchain, so it belongs there too. A DePool validator stakes
    through its DePool and keeps the node's own workchain, where fees are
    lower.
    """
    if configuration.validator_type(config) in ("solo", "nominator"):
        return -1
    settings = dict(
        configuration.DEFAULT_ENVIRONMENT, **(config.get("settings") or {})
    )
    return settings["NODE_WC"]


def require_validator_type(config, expected, alternative):
    """Keep DePool actions off a solo validator, and the reverse."""
    actual = configuration.validator_type(config)
    if actual != expected:
        raise process.OperationError(
            f"This node is a {actual} validator; use evernode {alternative}"
        )
    return actual


def _require_stage(config, mode, stage):
    profile = node_wallet_profile(config)
    if profile["mode"] != mode:
        raise process.OperationError(
            f"This action is available only for a {mode} wallet setup"
        )
    if config.get("setup_stage", "node-started") != stage:
        raise process.OperationError(f"This action requires setup stage {stage}")
    return profile


def _read_phrase(path):
    storage.validate_private_path(path)
    phrase = path.read_text().strip()
    if "\n" in phrase or "\r" in phrase or len(phrase.split()) < 12:
        raise process.OperationError(f"Invalid seed phrase file: {path.name}")
    return phrase


def _new_wallet_phrases(store, config, custodians):
    directory = seed_directory(store, config)
    storage.validate_private_path(directory)
    return [
        _read_phrase(directory / f"{config['name']}_seed_{index}.txt")
        for index in range(1, custodians + 1)
    ]


def _depool_seed(store, config):
    path = (
        store.node_directory(config["name"])
        / "keys"
        / ("DPKeys_" + config["name"])
        / "depool_seed.txt"
    )
    return _read_phrase(path)


def wallet_create(store, config):
    profile = _require_stage(config, "new", "node-started")
    details = prepare_wallet(store, config)
    profile["wallet_address"] = details["address"]
    config["wallet"] = profile
    config["setup_stage"] = "wallet-created"
    return (
        config,
        {
            "stage": config["setup_stage"],
            "wallet": details,
            "next": f"Fund {details['address']}, then run: evernode wallet deploy -n {config['name']}",
        },
        _new_wallet_phrases(store, config, profile["custodians"]),
    )


def wallet_recover(store, config):
    profile = _require_stage(config, "import", "node-started")
    details = prepare_wallet(store, config, imported=True)
    observed = scripts.verify_imported_safe(store, config, profile["wallet_address"])
    _check_imported_wallet(profile, details["public_keys"], observed)
    _install_imported_wallet_files(store, config, profile["wallet_address"])
    config["setup_stage"] = "wallet-recovered"
    return config, {
        "stage": config["setup_stage"],
        "wallet": details,
        "next": f"Run: evernode wallet verify -n {config['name']}",
    }


def wallet_deploy(store, config):
    profile = _require_stage(config, "new", "wallet-created")
    scripts.execute_validator_script(
        store,
        config,
        "Msig_deploy.sh",
        "--",
        "Safe",
        str(profile["custodians"]),
        str(profile["required_signatures"]),
        timeout=600,
        sensitive=True,
    )
    scripts.wait_for_active_account(
        store, config, profile["wallet_address"], "Safe wallet"
    )
    config["setup_stage"] = "wallet-deployed"
    return config, {
        "stage": config["setup_stage"],
        "next": next_setup_command(config) or "",
    }


def wallet_verify(store, config):
    profile = _require_stage(config, "import", "wallet-recovered")
    observed = scripts.verify_imported_safe(store, config, profile["wallet_address"])
    local_public_keys = _public_key_summary(
        store.node_directory(config["name"]) / "keys", config["name"]
    )
    _check_imported_wallet(profile, local_public_keys, observed)
    if configuration.validator_type(config) in ("solo", "nominator"):
        # Nothing this CLI manages links these modes to a second contract that
        # verifying the wallet could check: a solo validator stakes from the
        # Safe itself, and a nominator validator's contract is derived from it
        # and checked by 'nominator verify'. So the import ends here.
        config["setup_stage"] = "imported"
        nominator = configuration.validator_type(config) == "nominator"
        return config, {
            "stage": config["setup_stage"],
            "wallet": observed,
            "next": (
                f"Run: evernode nominator prepare -n {config['name']}"
                if nominator
                else f"Run: evernode stake status -n {config['name']} to see what "
                "the elector still holds for this wallet, then evernode stake set"
            ),
        }
    config["setup_stage"] = "wallet-verified"
    return config, {
        "stage": config["setup_stage"],
        "wallet": observed,
        "next": f"Run: evernode depool verify -n {config['name']}",
    }


def depool_prepare(store, config):
    require_validator_type(config, "depool", "stake")
    profile = _require_stage(config, "new", "wallet-deployed")
    scripts.execute_validator_script(
        store, config, "DePool-prep.sh", timeout=300, sensitive=True
    )
    address_file = store.node_directory(config["name"]) / "keys" / "depool.addr"
    storage.validate_private_path(address_file)
    address = address_file.read_text().strip()
    if not ADDRESS_PATTERN.fullmatch(address):
        raise process.OperationError(
            "DePool-prep.sh did not create a valid DePool address"
        )
    ensure_unassigned(store, config, profile["wallet_address"], address)
    profile["depool_address"] = address
    config["wallet"] = profile
    config["setup_stage"] = "depool-prepared"
    return (
        config,
        {
            "stage": config["setup_stage"],
            "depool_address": address,
            "next": f"Fund {address}, then run: evernode depool deploy -n {config['name']}",
        },
        _depool_seed(store, config),
    )


def depool_deploy(store, config):
    require_validator_type(config, "depool", "stake")
    profile = _require_stage(config, "new", "depool-prepared")
    scripts.execute_validator_script(
        store, config, "DePool_deploy.sh", timeout=600, sensitive=True
    )
    scripts.wait_for_active_account(
        store, config, profile["depool_address"], "DePool"
    )
    config["setup_stage"] = "depool-deployed"
    return config, {
        "stage": config["setup_stage"],
        "next": f"Run: evernode depool stake-initial -n {config['name']}",
    }


def depool_stake_initial(store, config):
    require_validator_type(config, "depool", "stake")
    _require_stage(config, "new", "depool-deployed")
    assurance = str(config.get("settings", {}).get("ValidatorAssuranceT", 50000))
    scripts.execute_validator_script(
        store,
        config,
        "stake_to_depool.sh",
        "ordinary",
        "--",
        "--",
        assurance,
        timeout=600,
        sensitive=True,
        # Setup may be retried; do not transfer the assurance twice.
        variables={"EVERNODE_INITIAL_STAKE": "yes"},
    )
    config["setup_stage"] = "ready-for-elections"
    return config, {
        "stage": config["setup_stage"],
        "next": f"Run: evernode election start -n {config['name']}",
    }


OPERATIONAL_STAGES = ("ready-for-elections", "imported")


def _require_operational_depool(config):
    """Stake operations need a deployed or imported DePool, in either mode."""
    profile = node_wallet_profile(config)
    if config.get("setup_stage") not in OPERATIONAL_STAGES:
        raise process.OperationError(
            "Complete wallet/DePool setup before running DePool stake operations"
        )
    if not profile["depool_address"]:
        raise process.OperationError("This node has no DePool address")
    return profile


def depool_info(store, config):
    require_validator_type(config, "depool", "stake")
    """Read the DePool, its rounds and this node's own participant stake."""
    profile = _require_operational_depool(config)
    observed = scripts.read_depool_state(
        store, config, profile["depool_address"], profile["wallet_address"]
    )
    return config, {
        "depool_address": profile["depool_address"],
        "wallet_address": profile["wallet_address"],
        **observed,
    }


def depool_stake(store, config, *, amount):
    require_validator_type(config, "depool", "stake")
    """Add an ordinary stake to this node's DePool from its own Safe.

    A DePool v3 round pipeline holds four rounds, and a completed round's stake
    is reinvested into the current pooling round rather than the next one being
    elected. One stake tranche therefore funds alternating rounds; funding every
    round needs a second tranche.
    """
    profile = _require_operational_depool(config)
    if amount < 1:
        raise process.OperationError("Stake amount must be at least one token")
    scripts.execute_validator_script(
        store,
        config,
        "stake_to_depool.sh",
        "ordinary",
        "--",
        "--",
        str(amount),
        timeout=600,
        sensitive=True,
    )
    return config, {
        "operation": "ordinary",
        "depool_address": profile["depool_address"],
        "requested_tokens": amount,
        "note": "The stake joins the DePool's current pooling round",
    }


def confirm_wallet_transaction(store, config, transaction_id, check):
    """Add the remaining signatures to one explicitly named Safe transaction.

    A multisig transaction that keeps fewer than its required signatures never
    executes and blocks later operations on that wallet, which is the stuck
    state an interrupted election attempt leaves behind. This is a property of
    the Safe, not of a DePool, so a solo validator reaches the same state and
    resolves it the same way. The reference script refuses to sign a list, so
    the operator names the transaction they reviewed.
    """
    profile = node_wallet_profile(config)
    if config.get("setup_stage") not in OPERATIONAL_STAGES:
        raise process.OperationError(
            "This action needs a finished setup; run: "
            + (next_setup_command(config) or "evernode node status")
        )
    if not re.fullmatch(r"[0-9]{1,30}", str(transaction_id)):
        raise process.OperationError("Transaction id must be a decimal number")
    scripts.execute_validator_script(
        store,
        config,
        "Sign_Trans.sh",
        config["name"],
        str(transaction_id),
        timeout=600,
        sensitive=True,
    )
    return config, {
        "operation": "confirm",
        "wallet_address": profile["wallet_address"],
        "transaction_id": str(transaction_id),
        "next": f"Check: evernode {check} -n {config['name']}",
    }


def depool_confirm(store, config, *, transaction_id):
    require_validator_type(config, "depool", "stake")
    _require_operational_depool(config)
    return confirm_wallet_transaction(store, config, transaction_id, "depool info")


def stake_confirm(store, config, *, transaction_id):
    """A solo validator's wallet can hold a half-signed transaction too."""
    require_validator_type(config, "solo", "depool")
    return confirm_wallet_transaction(store, config, transaction_id, "stake status")


def depool_withdraw(store, config, *, amount=None, withdraw_all=False, cancel=False):
    require_validator_type(config, "depool", "stake")
    """Withdraw this node's own ordinary stake, or cancel a pending withdrawal.

    DePool withdrawals are not immediate: the contract releases a stake when
    the round holding it completes, so the Safe balance changes later.
    """
    profile = _require_operational_depool(config)
    if sum((amount is not None, withdraw_all, cancel)) != 1:
        raise process.OperationError("Choose exactly one of --amount, --all, --cancel")
    if amount is not None and amount < 1:
        raise process.OperationError("Withdrawal amount must be at least one token")
    if withdraw_all:
        operation, extra = "WD-all", ()
    elif cancel:
        operation, extra = "cancelWithdrawal", ()
    else:
        operation, extra = "WD-part", (str(amount),)
    scripts.execute_validator_script(
        store,
        config,
        "stake_to_depool.sh",
        operation,
        "--",
        "--",
        *extra,
        timeout=600,
        sensitive=True,
    )
    return config, {
        "operation": operation,
        "depool_address": profile["depool_address"],
        "requested_tokens": amount,
        "note": (
            "Reinvestment resumed for the next round"
            if cancel
            else "The DePool releases a withdrawn stake when its round completes; "
            "check evernode depool info for the pending withdrawValue"
        ),
    }


def depool_replenish(store, config, *, amount):
    require_validator_type(config, "depool", "stake")
    """Top up the DePool's own operating balance from this node's Safe."""
    profile = _require_operational_depool(config)
    if amount < 1:
        raise process.OperationError("Replenish amount must be at least one token")
    scripts.execute_validator_script(
        store,
        config,
        "stake_to_depool.sh",
        "replenish",
        "--",
        "--",
        str(amount),
        timeout=600,
        sensitive=True,
    )
    return config, {
        "operation": "replenish",
        "depool_address": profile["depool_address"],
        "requested_tokens": amount,
    }


def depool_verify(store, config):
    require_validator_type(config, "depool", "stake")
    profile = _require_stage(config, "import", "wallet-verified")
    observed = scripts.verify_imported_depool(store, config, profile["depool_address"])
    if observed["validator_wallet"].lower() != profile["wallet_address"].lower():
        raise process.OperationError(
            "Imported DePool is linked to a different validator wallet"
        )
    storage.write_private_text_atomically(
        store.node_directory(config["name"]) / "keys" / "depool.addr",
        profile["depool_address"] + "\n",
    )
    config["setup_stage"] = "imported"
    return config, {
        "stage": config["setup_stage"],
        "depool_address": profile["depool_address"],
        "next": f"Run: evernode election start -n {config['name']} when the former node is fenced",
    }


def next_setup_command(config):
    name = config["name"]
    shared = {
        ("new", "node-started"): f"evernode wallet create -n {name}",
        ("new", "wallet-created"): f"evernode wallet deploy -n {name}",
        ("new", "ready-for-elections"): f"evernode election start -n {name}",
        ("import", "node-started"): f"evernode wallet recover -n {name}",
        ("import", "wallet-recovered"): f"evernode wallet verify -n {name}",
        ("import", "imported"): f"evernode election start -n {name}",
    }
    actions = {
        "depool": {
            ("new", "wallet-deployed"): f"evernode depool prepare -n {name}",
            ("new", "depool-prepared"): f"evernode depool deploy -n {name}",
            ("new", "depool-deployed"): f"evernode depool stake-initial -n {name}",
            ("import", "wallet-verified"): f"evernode depool verify -n {name}",
        },
        # A nominator validator's principal lives in a contract, so the
        # contract has to exist and be checked before a stake means anything.
        "nominator": {
            # The contract's address is derived from this node's Safe, so the
            # Safe comes first and the shared wallet stages apply unchanged.
            # Only once it exists can the contract be prepared.
            ("new", "wallet-deployed"): f"evernode nominator prepare -n {name}",
            ("new", "nominator-prepared"): f"evernode nominator deploy -n {name}",
            ("new", "nominator-deployed"): f"evernode nominator verify -n {name}",
            ("new", "nominator-verified"): f"evernode stake set -n {name} --mode balanced",
            ("import", "imported"): f"evernode nominator prepare -n {name}",
            ("import", "nominator-prepared"): f"evernode nominator deploy -n {name}",
            ("import", "nominator-deployed"): f"evernode nominator verify -n {name}",
            ("import", "nominator-verified"): f"evernode stake set -n {name} --mode balanced",
            ("import", "ready-for-elections"): f"evernode election start -n {name}",
        },
        # A solo validator needs a stake on record before it can bid, whether
        # its Safe is new or recovered from phrases; an imported node would
        # otherwise reach elections with no decided amount.
        "solo": {
            ("new", "wallet-deployed"): f"evernode stake set -n {name} --mode balanced",
            ("import", "imported"): f"evernode stake set -n {name} --mode balanced",
            ("import", "ready-for-elections"): f"evernode election start -n {name}",
        },
    }[configuration.validator_type(config)]
    profile = config.get("wallet")
    return (
        {**shared, **actions}.get((profile.get("mode"), config.get("setup_stage")))
        if isinstance(profile, dict)
        else None
    )
