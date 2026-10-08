"""Wallet-profile state and root-only seed recovery helpers."""

import json
import os
import re

from . import process, scripts, storage

ADDRESS_PATTERN = re.compile(r"^-?[0-9]+:[0-9a-fA-F]{64}$")


def validate_wallet_profile(profile):
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
    if profile["mode"] == "import" and (
        profile["wallet_address"] is None or profile["depool_address"] is None
    ):
        raise process.OperationError(
            "An imported validator requires expected wallet and DePool addresses"
        )
    return profile


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


def store_import_seeds(store, config, phrases):
    """Write phrases only to the files read by Msig-prep.sh, mode 0600."""
    profile = validate_wallet_profile(config["wallet"])
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


def prepare_wallet(store, config):
    """Use the reference generator and return only non-secret wallet facts."""
    profile = validate_wallet_profile(config["wallet"])
    scripts.execute_validator_script(
        store,
        config,
        "Msig-prep.sh",
        "--",
        "Safe",
        str(profile["custodians"]),
        "0",
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
    if (
        profile["wallet_address"]
        and address.lower() != profile["wallet_address"].lower()
    ):
        raise process.OperationError(
            "Recovered wallet address does not match the supplied address"
        )
    ensure_unassigned(store, config, address, profile["depool_address"])
    return {
        "address": address,
        "public_keys": _public_key_summary(keys, config["name"]),
        "key_directory": str(keys),
    }


def _require_stage(config, mode, stage):
    profile = validate_wallet_profile(config["wallet"])
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
    details = prepare_wallet(store, config)
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
    config["setup_stage"] = "wallet-deployed"
    return config, {
        "stage": config["setup_stage"],
        "next": f"Run: evernode depool prepare -n {config['name']}",
    }


def wallet_verify(store, config):
    profile = _require_stage(config, "import", "wallet-recovered")
    observed = scripts.verify_imported_safe(store, config, profile["wallet_address"])
    if (observed["custodians"], observed["required_signatures"]) != (
        profile["custodians"],
        profile["required_signatures"],
    ):
        raise process.OperationError(
            "Imported wallet custodians or signature threshold do not match the supplied profile"
        )
    config["setup_stage"] = "wallet-verified"
    return config, {
        "stage": config["setup_stage"],
        "wallet": observed,
        "next": f"Run: evernode depool verify -n {config['name']}",
    }


def depool_prepare(store, config):
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
    _require_stage(config, "new", "depool-prepared")
    scripts.execute_validator_script(
        store, config, "DePool_deploy.sh", timeout=600, sensitive=True
    )
    config["setup_stage"] = "depool-deployed"
    return config, {
        "stage": config["setup_stage"],
        "next": f"Run: evernode depool stake-initial -n {config['name']}",
    }


def depool_stake_initial(store, config):
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
    )
    config["setup_stage"] = "ready-for-elections"
    return config, {
        "stage": config["setup_stage"],
        "next": f"Run: evernode election start -n {config['name']}",
    }


def depool_verify(store, config):
    profile = _require_stage(config, "import", "wallet-verified")
    scripts.verify_imported_depool(store, config, profile["depool_address"])
    config["setup_stage"] = "imported"
    return config, {
        "stage": config["setup_stage"],
        "depool_address": profile["depool_address"],
        "next": f"Run: evernode election start -n {config['name']} when the former node is fenced",
    }


def next_setup_command(config):
    name = config["name"]
    actions = {
        ("new", "node-started"): f"evernode wallet create -n {name}",
        ("new", "wallet-created"): f"evernode wallet deploy -n {name}",
        ("new", "wallet-deployed"): f"evernode depool prepare -n {name}",
        ("new", "depool-prepared"): f"evernode depool deploy -n {name}",
        ("new", "depool-deployed"): f"evernode depool stake-initial -n {name}",
        ("new", "ready-for-elections"): f"evernode election start -n {name}",
        ("import", "node-started"): f"evernode wallet recover -n {name}",
        ("import", "wallet-recovered"): f"evernode wallet verify -n {name}",
        ("import", "wallet-verified"): f"evernode depool verify -n {name}",
        ("import", "imported"): f"evernode election start -n {name}",
    }
    profile = config.get("wallet")
    return (
        actions.get((profile.get("mode"), config.get("setup_stage")))
        if isinstance(profile, dict)
        else None
    )
