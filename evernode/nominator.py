"""The staking contract of a validator whose capital is held by a cold owner.

A nominator validator keeps its principal in a single-nominator contract in
the masterchain. The contract knows two addresses: a cold **owner**, which may
withdraw, replace the hot wallet and upgrade the code, and a hot **validator
wallet**, which may only tell it to bid and to recover. The server holds the
hot wallet's key and nothing else, so losing the server does not lose the
stake - which is the whole reason this mode exists beside solo staking.

The hot wallet is the node's own Safe, the same wallet solo staking uses. That
is deliberate: the elector conversation is unchanged and only the account that
sends it differs, so the kit's own election scripts carry the instructions,
with the signing, resume and multi-custodian handling they already have. What
is left here is what the kit cannot do: compile the contract, derive its
address, deploy it, and prepare the messages only the owner may authorize.

Operations the owner may perform are *prepared* here and signed somewhere
else: those functions write a message body and say what to do with it, and
never ask for an owner key.
"""

import json
import os
import re
import shutil
import sys
import time

from . import configuration, docker, process, storage

NANO = 10**9
UNKNOWN = "unknown"

# The node container already mounts the node's elections directory, so
# artifacts placed here are readable by its console without a new mount and
# without recreating a node that existed before this mode.
WORK_SUBDIRECTORY = "elections/nominator"
NODE_WORK_DIRECTORY = "/ever-node/elections/nominator"


def _require_nominator(config):
    actual = configuration.validator_type(config)
    if actual != "nominator":
        raise process.OperationError(
            f"This node is a {actual} validator; the nominator commands apply "
            "only to a validator whose stake is held by a single-nominator "
            "contract"
        )


def _settings(config):
    return dict(configuration.DEFAULT_ENVIRONMENT, **(config.get("settings") or {}))


def work_directory(store, config, create=False):
    path = store.node_directory(config["name"]) / WORK_SUBDIRECTORY
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    storage.validate_private_path(path)
    return path


def hot_wallet_address(config):
    """The Safe that instructs the contract, which is this node's own wallet.

    Nothing about it is nominator-specific: it is created, deployed, recovered
    and verified by the ordinary wallet commands, and its only privilege at
    the contract is to say "bid" and "recover".
    """
    profile = config.get("wallet") or {}
    address = profile.get("wallet_address")
    if not address:
        raise process.OperationError(
            "This node has no wallet yet, and the contract's address depends "
            "on it; run: evernode wallet create -n " + config["name"]
        )
    if not str(address).startswith("-1:"):
        raise process.OperationError(
            "The wallet that instructs a nominator contract must be in the "
            f"masterchain; {address} is not"
        )
    return address


def _run_tools(store, config, program, timeout=300, environment=None):
    """Run a shell program inside the pinned contract-tool image.

    The tools need Node.js; a validator host should not. They see only this
    node's nominator directory - no key is mounted, because nothing in the
    image signs: the Safe signs through the kit's scripts and the node's
    console broadcasts.
    """
    image = config.get("nominator_tools_image")
    if not image:
        raise process.OperationError(
            "This node has no contract-tool image recorded; run: evernode "
            f"nominator prepare -n {config['name']}"
        )
    work = work_directory(store, config, create=True)
    return process.execute_command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{work}:/work",
            "-w",
            "/opt/nominator",
            *(
                part
                for name, value in (environment or {}).items()
                for part in ("-e", f"{name}={value}")
            ),
            image,
            program,
        ],
        timeout=timeout,
    )


def _console(config, command, timeout=60):
    container = configuration.node_container_name(config)
    obj = docker.inspect_container(container)
    if not obj or obj["State"]["Status"] != "running":
        raise process.OperationError(
            "The node container is not running, and its console is the only "
            "way to reach the chain; start it first"
        )
    return process.execute_command(
        [
            "docker",
            "exec",
            container,
            "console",
            "-C",
            "/ever-node/node_cfg/console.json",
            "-jc",
            command,
        ],
        timeout=timeout,
    )


def _account(config, address):
    """Read one account's status and balance through the node's console."""
    result = _console(config, f"getaccount {address}")
    try:
        value = json.loads(result.stdout)
    except ValueError:
        # An absent account is reported rather than raised: the deployment
        # flow has to wait for an address to come into existence.
        return {"acc_type": "Unknown", "balance": "0"}
    if not isinstance(value, dict):
        return {"acc_type": "Unknown", "balance": "0"}
    return value


def _manifest(store, config):
    path = work_directory(store, config) / "manifest.json"
    if not path.is_file():
        raise process.OperationError(
            "This node has no prepared contract; run: evernode nominator "
            f"prepare -n {config['name']}"
        )
    try:
        manifest = json.loads(path.read_text())
    except ValueError as exc:
        raise process.OperationError("The prepared contract manifest is unreadable") from exc
    # Files prepared for another contract than the node's: the node bids from
    # the recorded address, so acting on these would act on the wrong account.
    recorded = (config.get("nominator") or {}).get("address")
    if recorded and manifest.get("address") != recorded:
        raise process.OperationError(
            f"The prepared contract files are for {manifest.get('address')}, but this "
            f"node's contract is {recorded}. Run: evernode nominator prepare -n "
            f"{config['name']} (from the source that contract was built from)"
        )
    return manifest


def owner_address(config):
    value = (config.get("settings") or {}).get("NOMINATOR_OWNER")
    if not value:
        raise process.OperationError(
            "This node has no owner address recorded, so there is nothing that "
            "could authorize a withdrawal"
        )
    return value


def nominator_address(config):
    value = (config.get("settings") or {}).get("NOMINATOR_ADDR")
    if not value:
        raise process.OperationError(
            "This node has no nominator contract yet; run: evernode nominator "
            f"prepare -n {config['name']}"
        )
    return value


PREPARE_PROGRAM = r"""set -e
# Addresses here start with '-1:' or '0:': the contract and its Safe are in
# the masterchain, and the owner is in either. The script-file form of node
# passes a '-1:' argument through, but 'node -e' reads it as one of node's own
# options, so the inline snippets below take their addresses from the
# environment instead of from argv.
node tools/cells.cjs compile /work/code.boc > /work/compile.json
# Assigned, then exported: "export VAR=$(...)" takes export's status, so set -e
# would let a failed step through and save an empty address.
POOL=$(node tools/cells.cjs init /work/code.boc "$OWNER" "$VALIDATOR" /work/init.boc)
FACTORY=$(node tools/cells.cjs factory /work/init.boc "$DEPLOY_VALUE" /work/factory-deploy.boc)
export POOL FACTORY
node -e '
  const fs = require("fs");
  const pinned = require("/opt/nominator/PINNED.json");
  const compiled = JSON.parse(fs.readFileSync("/work/compile.json", "utf8"));
  if (compiled.codeHash !== pinned.codeHash) {
    throw Error("compiled " + compiled.codeHash + " but pinned " + pinned.codeHash);
  }
  fs.writeFileSync("/work/manifest.json", JSON.stringify({
    upstreamRepository: pinned.upstreamRepository,
    upstreamCommit: pinned.upstreamCommit,
    upstreamVersion: pinned.upstreamVersion,
    compiler: pinned.compiler,
    codeHash: compiled.codeHash,
    codeHashBase64: compiled.codeHashBase64,
    owner: process.env.OWNER,
    validator: process.env.VALIDATOR,
    address: process.env.POOL,
    factory: process.env.FACTORY,
    deployValueNanotokens: process.env.DEPLOY_VALUE,
  }, null, 2) + "\n");
'
cat /work/manifest.json
"""


# What the image is built from. Everything else in a vendored checkout -
# installed packages, build output, a developer's scratch files - is not in
# the image and must not decide its identity.
TOOL_SOURCES = ("contracts", "tools", "package.json", "package-lock.json", "PINNED.json")


def _stage_tool_sources(source, staging):
    """Copy exactly the files the image is built from into a build context.

    A vendored checkout that has had 'npm install' run in it holds symlinks
    and hundreds of megabytes that the image does not use: the compiler comes
    from the lockfile inside the build. Copying the directory wholesale made
    preparation fail on those symlinks, and would have rebuilt the image
    whenever an unrelated file next to it changed.
    """
    root = staging / "src"
    root.mkdir(parents=True)
    missing = [name for name in TOOL_SOURCES if not (source / name).exists()]
    if missing:
        raise process.OperationError(
            f"{source} is missing " + ", ".join(missing) + "; it does not look "
            "like the vendored single-nominator source"
        )
    for name in TOOL_SOURCES:
        item = source / name
        if item.is_dir():
            shutil.copytree(item, root / name, symlinks=False)
        else:
            shutil.copyfile(item, root / name)
    return root


def build_tools_image(store, config, source, recipe, timeout=1800):
    """Build the pinned contract-tool image, or reuse the one already built.

    The tag is a fingerprint of the whole build context, recipe included, so
    an image built from different vendored sources or a changed recipe is a
    different image rather than the same tag with different bytes. The build
    itself fails if the contract does not compile to its recorded code hash,
    so a usable image is evidence that the bytecode is the reviewed one.
    """
    staging = work_directory(store, config, create=True) / ".tools-build"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(mode=0o700)
    try:
        _stage_tool_sources(source, staging)
        shutil.copyfile(recipe, staging / "Dockerfile")
        tag = "local/evernode-nominator-tools:" + storage.calculate_directory_fingerprint(
            staging
        )[:16]
        if docker.image_exists(tag):
            return tag, False
        process.execute_command(
            [
                "docker",
                "build",
                "--tag",
                tag,
                "--file",
                str(staging / "Dockerfile"),
                str(staging),
            ],
            timeout=timeout,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return tag, True


def resolve_contract_source(config, requested=None):
    """Locate the single-nominator source this node compiles.

    A directory named with --contract-source comes first, then one an earlier
    prepare recorded while it still exists, then one beside a kit named with
    --project-source. By default it is the copy installed with evernode, which
    is never recorded: a later prepare takes the copy installed then, the
    contract whose code hash that evernode pins.
    """
    from pathlib import Path

    if requested:
        path = Path(requested).expanduser().absolute()
        if not path.is_dir():
            raise process.OperationError(f"{path} does not exist or is not a directory")
        if not (path / "PINNED.json").is_file():
            raise process.OperationError(
                f"{path} does not look like the vendored single-nominator "
                "source: it has no PINNED.json"
            )
        return path
    candidates = [config.get("nominator_source")]
    local_kit = (config.get("source_spec") or {}).get("local")
    if local_kit:
        # Beside a kit named with --project-source, as in a checkout.
        candidates.append(str(Path(local_kit).parent / "single-nominator"))
    for candidate in candidates:
        if candidate and (Path(candidate) / "PINNED.json").is_file():
            return Path(candidate).absolute()
    if (configuration.BUNDLED_CONTRACT_SOURCE / "PINNED.json").is_file():
        return configuration.BUNDLED_CONTRACT_SOURCE
    raise process.OperationError(
        "evernode's single-nominator source is missing from "
        f"{configuration.BUNDLED_CONTRACT_SOURCE}: reinstall evernode, or name "
        "one with --contract-source"
    )


def require_tools_image(config):
    """The image a nominator bid splices its stake with. Only a bid runs it, so
    no container keeps it in use, and an image prune removes it."""
    image = (config.get("settings") or {}).get("NOMINATOR_TOOLS_IMAGE")
    if not image or not docker.image_exists(image):
        raise process.OperationError(
            f"The nominator tools image {image or ''} is not on this host, so no bid "
            f"can be prepared; run: evernode nominator prepare -n {config['name']}"
        )


# Stages at which this node's contract is on chain.
CONTRACT_STAGES = ("nominator-deployed", "nominator-verified", "ready-for-elections")


def prepare(store, config, *, source, recipe, new_contract=False):
    """Compile the contract and derive the address it will have.

    Nothing here reaches the chain or spends anything: it establishes which
    bytecode this node will deploy and which account it will be, so both can
    be checked before any principal exists. The address is a pure function of
    the code, the cold owner and this node's Safe, so all three have to be
    settled first.
    """
    _require_nominator(config)
    owner = owner_address(config)
    validator = hot_wallet_address(config)
    if validator.lower() == owner.lower():
        raise process.OperationError(
            "The owner and the wallet that instructs the contract must be "
            "different accounts; one address holding both roles can withdraw "
            "the principal from the validator server, which is the whole "
            "thing this mode prevents"
        )
    settings = _settings(config)
    source = resolve_contract_source(config, source)
    tag, built = build_tools_image(store, config, source, recipe)
    config["nominator_tools_image"] = tag
    # The copy installed with evernode is found again by default; its path
    # would go stale with the next upgrade or a rebuilt virtual environment.
    if source.resolve() == configuration.BUNDLED_CONTRACT_SOURCE:
        config.pop("nominator_source", None)
    else:
        config["nominator_source"] = str(source)
    work = work_directory(store, config, create=True)
    # Built aside and moved in only once the address is accepted: deploy,
    # verify, info and the owner's requests all read these files, so a refused
    # prepare must leave the deployed contract's own in place.
    staging = work / ".prepare"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(mode=0o700)
    try:
        deploy_value = str(settings["NOMINATOR_DEPLOY_VALUE"])
        result = process.execute_command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{staging}:/work",
                "-e",
                f"OWNER={owner}",
                "-e",
                f"VALIDATOR={validator}",
                "-e",
                f"DEPLOY_VALUE={deploy_value}",
                "-w",
                "/opt/nominator",
                tag,
                PREPARE_PROGRAM,
            ],
            timeout=600,
        )
        try:
            manifest = json.loads(result.stdout[result.stdout.index("{"):])
        except (ValueError, IndexError) as exc:
            raise process.OperationError(
                "Contract preparation did not produce a manifest"
            ) from exc
        for name in ("code.boc", "init.boc", "factory-deploy.boc"):
            (staging / name).chmod(0o600)
        # Other code gives another address. A node whose contract is deployed would
        # then bid from an empty contract while its stake sits in the old one.
        deployed = (config.get("nominator") or {}).get("address")
        if (
            deployed
            and config.get("setup_stage") in CONTRACT_STAGES
            and manifest["address"] != deployed
            and not new_contract
        ):
            raise process.OperationError(
                f"This node's contract is deployed at {deployed}, and this code gives "
                f"{manifest['address']}. Prepare it from the source the deployed one was "
                "built from, or use --new-contract to move the node to the new address"
            )
        for item in staging.iterdir():
            os.replace(item, work / item.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    settings["NOMINATOR_ADDR"] = manifest["address"]
    # The election scripts splice the stake into the elector's request with
    # this image, so the node's environment has to name it.
    settings["NOMINATOR_TOOLS_IMAGE"] = tag
    config["settings"] = settings
    config["nominator"] = {
        "address": manifest["address"],
        "factory": manifest["factory"],
        "hot_wallet": manifest["validator"],
        "owner": manifest["owner"],
        "code_hash": manifest["codeHash"],
        "upstream_commit": manifest["upstreamCommit"],
    }
    configuration.write_node_environment(
        store.node_directory(config["name"]) / "scripts" / "env.sh",
        {
            "NOMINATOR_ADDR": manifest["address"],
            "NOMINATOR_TOOLS_IMAGE": tag,
        },
    )
    # An imported node's contract exists already, and so does a deployed one's:
    # it is checked next, not deployed.
    on_chain = (
        (config.get("wallet") or {}).get("mode") == "import"
        or config.get("setup_stage") in CONTRACT_STAGES
    )
    if config.get("setup_stage") in (
        "wallet-deployed",
        "imported",
        "preparing",
        "nominator-prepared",
    ):
        config["setup_stage"] = "nominator-prepared"
    return config, {
        "tools_image": tag,
        "tools_image_built": built,
        "contract_source": str(source),
        "manifest": manifest,
        "artifacts": str(work),
        "note": (
            "This is the existing contract's address: nominator verify checks the "
            "contract on chain against this code, owner and wallet."
            if on_chain
            else "The contract does not exist yet. The address above is fixed by "
            "this code and this owner/wallet pair: a different pair is a "
            "different account, so changing either one means deploying again."
        ),
        "next": (
            f"evernode nominator verify -n {config['name']}"
            if on_chain
            else f"evernode nominator deploy -n {config['name']}"
        ),
    }


# How often deploy sends the deployer's trigger, waiting a minute after each.
TRIGGER_SENDS = 3


def _broadcast(config, name):
    """Submit a signed message through the node's own console."""
    return _console(config, f"sendmessage {NODE_WORK_DIRECTORY}/{name}")


def _wait_for(config, address, predicate, *, attempts=45, delay=4, what="account"):
    """Poll one account until the chain shows the state we need.

    Submitting a message only means the node accepted it for broadcast, so
    every step that changes an account waits for the account itself rather
    than reporting the submission as the result.
    """
    import time

    observed = {}
    for attempt in range(attempts):
        observed = _account(config, address)
        if predicate(observed):
            return observed
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise process.OperationError(
        f"The {what} did not reach the expected state within "
        f"{attempts * delay}s (last reading: {observed.get('acc_type')}, "
        f"balance {observed.get('balance')})"
    )


def deploy(store, config):
    """Fund the one-shot deployer and let it create the contract.

    A wallet's ordinary transfer cannot carry a StateInit, so the contract is
    created by a deployer whose code can only send one prepared message and
    delete itself. It has no key, so triggering it is harmless and its funds
    cannot be redirected anywhere but the contract. The funding comes from the
    node's Safe through the same signing path every other wallet operation
    uses, so a multi-custodian Safe deploys exactly as it bids.
    """
    _require_nominator(config)
    from . import scripts

    manifest = _manifest(store, config)
    settings = _settings(config)
    pool, factory = manifest["address"], manifest["factory"]
    wallet = hot_wallet_address(config)
    if manifest["validator"].lower() != wallet.lower():
        raise process.OperationError(
            f"The prepared contract expects {manifest['validator']} to "
            f"instruct it, but this node's wallet is now {wallet}. The "
            "address depends on that wallet, so prepare it again: evernode "
            f"nominator prepare -n {config['name']}"
        )
    deployed = _account(config, pool)
    if deployed.get("acc_type") == "Active":
        return config, {
            "nominator": pool,
            "result": "already deployed",
            "next": f"evernode nominator verify -n {config['name']}",
        }

    budget = int(settings["NOMINATOR_DEPLOY_VALUE"])
    funding = budget + int(settings["NOMINATOR_DEPLOY_MARGIN"])
    # With several signatures the payment is proposed, then approved; cut off in
    # between, it waits half-signed in the Safe until it expires an hour later.
    signatures = int((config.get("wallet") or {}).get("required_signatures") or 1)
    if signatures > 1:
        print(
            f"Paying the deployer takes {signatures} signatures from the Safe: "
            "do not interrupt (Ctrl+C) until the payment is reported done.",
            file=sys.stderr,
            flush=True,
        )
    funded = scripts.fund_nominator_deployer(
        store, config, wallet, factory, funding, budget
    )
    if signatures > 1:
        print(
            f"Deployer payment done (Safe transaction {funded['transaction_id']})."
            if funded.get("transaction_id")
            else "The deployer was already funded; nothing was paid.",
            file=sys.stderr,
            flush=True,
        )
    # The deployer is triggered by an unsigned external message: its code can
    # only send the message in its data, so nothing has to authorize it. One
    # the network drops is sent again: a later copy finds no deployer once the
    # first has run, since the deployer deletes itself.
    observed = None
    for _ in range(TRIGGER_SENDS):
        _broadcast(config, "factory-deploy.boc")
        try:
            observed = _wait_for(
                config,
                pool,
                lambda item: item.get("acc_type") == "Active",
                attempts=15,
                what="nominator contract",
            )
            break
        except process.OperationError:
            continue
    if observed is None:
        deployer = _account(config, factory).get("acc_type")
        raise process.OperationError(
            f"The nominator contract did not appear after {TRIGGER_SENDS} sends of the "
            f"deployer's trigger, a minute apart (deployer: {deployer}). "
            + (
                "The deployer is paid and still waiting: run evernode nominator "
                f"deploy -n {config['name']} again later"
                if deployer == "Uninit"
                else f"Check: evernode nominator info -n {config['name']}"
            )
        )
    config["setup_stage"] = "nominator-deployed"
    return config, {
        "nominator": pool,
        "result": "deployed",
        "balance_nanotokens": observed.get("balance"),
        "deployer": factory,
        "deployer_funding": funded,
        "deployer_state": _account(config, factory).get("acc_type"),
        "next": f"evernode nominator verify -n {config['name']}",
    }


VERIFY_PROGRAM = r"""set -e
node tools/cells.cjs account /work/deployed.account.boc
"""


def verify(store, config):
    """Check the deployed account against the manifest, before any principal.

    Three things have to hold and none can be assumed from a successful
    deployment: the account is active, its code is the bytecode that was
    reviewed, and its two roles are the pair that was intended. All of them
    are read out of the account state the node hands over, so the code hash
    is the hash of the bytecode that is actually there - a node's account
    summary does not carry one.
    """
    _require_nominator(config)
    manifest = _manifest(store, config)
    pool = manifest["address"]
    observed = _account(config, pool)
    if observed.get("acc_type") != "Active":
        raise process.OperationError(
            f"The nominator {pool} is {observed.get('acc_type')}, not Active; "
            f"run: evernode nominator deploy -n {config['name']}"
        )
    _console(
        config,
        f"getaccountstate {pool} {NODE_WORK_DIRECTORY}/deployed.account.boc",
    )
    result = _run_tools(store, config, VERIFY_PROGRAM)
    try:
        chain = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise process.OperationError(
            "Could not read the deployed contract's account state"
        ) from exc
    problems = []
    if chain.get("state") != "active":
        problems.append(f"the account is {chain.get('state')}, not active")
    if chain.get("address", "").lower() != pool.lower():
        problems.append(
            f"the account state is for {chain.get('address')}, not {pool}"
        )
    for field, label in (
        ("codeHash", "code hash"),
        ("owner", "owner"),
        ("validator", "hot wallet"),
    ):
        expected = manifest["codeHash" if field == "codeHash" else field]
        actual = chain.get(field)
        if not actual:
            problems.append(f"the account state carries no {label}")
        elif actual.lower() != expected.lower():
            problems.append(f"deployed {label} {actual} is not {expected}")
    if problems:
        raise process.OperationError(
            "The deployed contract is not the one that was prepared: "
            + "; ".join(problems)
        )
    if config.get("setup_stage") in ("nominator-deployed", "nominator-prepared"):
        config["setup_stage"] = "nominator-verified"
    return config, {
        "nominator": pool,
        "verified": True,
        "code_hash": chain["codeHash"],
        "upstream_commit": manifest["upstreamCommit"],
        "owner": chain["owner"],
        "hot_wallet": chain["validator"],
        "balance_nanotokens": chain["balance"],
        "next": f"evernode stake set -n {config['name']} --mode balanced",
    }


def info(store, config):
    """Report where this validator's money actually is.

    The contract's own get_pool_data() is a compatibility stub that reports
    fixed values and knows nothing about frozen stake, so everything here is
    read from the account itself and from the live elector instead.
    """
    _require_nominator(config)
    from . import scripts

    manifest = _manifest(store, config)
    settings = _settings(config)
    pool = manifest["address"]
    hot = manifest["validator"]
    contract = _account(config, pool)
    wallet = _account(config, hot)
    wallet_state = scripts.read_wallet_pending(store, config, hot)
    elector = scripts.read_elector_credit(store, config, pool)
    liquid = int(contract.get("balance") or 0)
    reserve = int(settings["NOMINATOR_RESERVE"])
    return {
        "nominator": pool,
        "owner": manifest["owner"],
        "hot_wallet": hot,
        "state": contract.get("acc_type"),
        # The states the stake passes through are different places, not
        # different names for one balance.
        "liquid_nanotokens": str(liquid),
        "withdrawable_nanotokens": str(max(0, liquid - reserve)),
        "reserve_nanotokens": str(reserve),
        "recoverable_nanotokens": elector.get("recoverable_nanotokens", "0"),
        "active_election_id": elector.get("active_election_id", "0"),
        "open_bid": _reading(
            lambda: _open_bid(store, config, int(elector.get("active_election_id") or 0))
        ),
        "at_elector": _reading(lambda: _rounds_at_elector(store, config)),
        "elector": elector.get("elector"),
        # The Safe pays for the instructions and holds nothing else. A
        # nominator validator therefore has three balances to keep healthy,
        # and an empty Safe stops the bidding while the principal looks fine.
        "wallet_gas_nanotokens": str(int(wallet.get("balance") or 0)),
        "wallet_state": wallet.get("acc_type"),
        "pending_wallet_transactions": wallet_state.get("pending_transactions", []),
        "configured_stake_tokens": settings["MSIG_FIX_STAKE"],
        "note": (
            "Frozen stake is held by the elector against this contract's "
            "address and cannot be withdrawn early. An owner withdrawal can "
            "only take liquid funds above the reserve."
        ),
    }


def _reading(read):
    """What one source says, or "unknown" when it cannot be read: one silent
    source must not hide everything else the report shows."""
    try:
        return read()
    except (process.OperationError, KeyError, TypeError, ValueError):
        return UNKNOWN


def _open_bid(store, config, election_id):
    """This node's bid in the election open now, as the elector lists it.

    None when no election is open or the node has no key for it: the kit makes
    the key just before it bids, so without one there is no bid.
    """
    from . import nodes, scripts

    if not election_id:
        return None
    key = next(
        (
            item
            for item in nodes.validator_keys(store, config) or []
            if item["election_id"] == election_id and item["adnl"]
        ),
        None,
    )
    if key is None:
        return None
    found = scripts.read_open_bid(store, config, key["adnl"])
    if found.get("status") == "unreadable":
        return UNKNOWN
    if found.get("status") != "found":
        return None
    stake = found["stake"]
    return {
        "election_id": str(election_id),
        "stake_nanotokens": str(int(stake, 16) if stake.lower().startswith("0x") else int(stake)),
    }


def _rounds_at_elector(store, config, now=None):
    """The rounds whose stake the elector still holds, and when each comes back.

    A round this node was elected for holds its stake until the set ends and
    the network's hold time (config param 15, stake_held_for) has passed. The
    stake is then recoverable, and the next election run recovers it. Amounts
    are not shown: they are in the elector's frozen records, not read here.
    """
    from . import nodes

    rounds = nodes.elected_rounds(store, config)
    if not rounds:
        return []
    held = int(nodes.fetch_chain_parameter(config, 15)["stake_held_for"])
    now = time.time() if now is None else now
    return [
        {
            "round": item["round"],
            "election_id": str(item["utime_since"]),
            "returns_about": time.strftime(
                "%Y-%m-%d %H:%M:%SZ", time.gmtime(item["utime_until"] + held)
            ),
        }
        for item in rounds
        if item["utime_until"] + held > now
    ]


OWNER_BODY_PROGRAM = r"""set -e
node tools/cells.cjs body $KIND /work/$OUT $ARGUMENT
node tools/cells.cjs base64 /work/$OUT
"""


def _owner_request(store, config, *, kind, out, argument="", description, warning=None):
    """Prepare a message only the owner may authorize, and stop there.

    The owner key is deliberately not on this server: it is the one thing
    that makes this mode safer than staking from a wallet the server holds.
    So these operations end with a body to sign elsewhere, never with a
    broadcast.
    """
    _require_nominator(config)
    manifest = _manifest(store, config)
    work = work_directory(store, config, create=True)
    result = _run_tools(
        store,
        config,
        OWNER_BODY_PROGRAM,
        environment={"KIND": kind, "OUT": out, "ARGUMENT": argument},
    )
    (work / out).chmod(0o600)
    body = result.stdout.strip().splitlines()[-1]
    settings = _settings(config)
    return {
        "operation": kind,
        "authorized_by": "owner",
        "owner": manifest["owner"],
        "destination": manifest["address"],
        "attach_value_nanotokens": str(settings["NOMINATOR_OP_FEE"]),
        "bounce": True,
        "body_file": str(work / out),
        "body_base64": body,
        "description": description,
        "warning": warning,
        "how_to_sign": (
            "Sign this body from the owner wallet on the machine that holds "
            f"its key: send {settings['NOMINATOR_OP_FEE']} nanotokens to "
            f"{manifest['address']} with this body attached and bounce set. "
            "An external message expires about a minute after signing, so "
            "sign immediately before submitting. Then hand the signed "
            "message back: evernode nominator submit -n "
            f"{config['name']} --message <signed.boc>"
        ),
    }


def withdraw(store, config, *, amount=None, withdraw_all=False):
    """Prepare an owner withdrawal of liquid funds above the reserve."""
    if withdraw_all == (amount is not None):
        raise process.OperationError("Use --amount N or --all, not both")
    if amount is not None and amount <= 0:
        raise process.OperationError("A withdrawal must be a positive number of tokens")
    if withdraw_all:
        # The contract treats the exact comment 'w' as "everything liquid".
        return _owner_request(
            store,
            config,
            kind="comment",
            out="withdraw-all.body.boc",
            argument="w",
            description=(
                "Withdraw every liquid token above the contract's reserve to "
                "the owner."
            ),
            warning=(
                "This takes the principal, not only the reward. It cannot "
                "touch stake still held by the elector, and taking the stake "
                "that backs this validator ends its validation."
            ),
        )
    return _owner_request(
        store,
        config,
        kind="withdraw",
        out="withdraw.body.boc",
        argument=str(amount * NANO),
        description=f"Withdraw {amount} tokens to the owner.",
        warning=(
            "The amount is clamped to the liquid balance minus the reserve, "
            "so a request larger than that withdraws what is available "
            "rather than failing."
        ),
    )


def rotate(store, config, *, validator_address):
    """Prepare an owner replacement of the hot wallet address."""
    if not re.fullmatch(r"-1:[0-9a-fA-F]{64}", str(validator_address)):
        raise process.OperationError(
            "The hot wallet instructs the elector through this contract, so "
            "it must be a masterchain address: -1: and 64 hex digits"
        )
    return _owner_request(
        store,
        config,
        kind="rotate",
        out="rotate.body.boc",
        argument=validator_address,
        description=(
            f"Authorize {validator_address} to instruct this contract instead "
            "of the current hot wallet."
        ),
        warning=(
            "This changes who may bid and recover. It does not move stake "
            "already held by the elector, and it does not rotate the node's "
            "consensus or ADNL keys. Once it executes, the contract ignores "
            "this node's Safe: stop this node's elections first (evernode "
            "election stop), because its bids would only spend gas."
        ),
    )


def submit(store, config, *, message):
    """Broadcast a message the owner signed elsewhere."""
    _require_nominator(config)
    from pathlib import Path

    source = Path(message).expanduser().absolute()
    if not source.is_file():
        raise process.OperationError(f"No such signed message: {source}")
    work = work_directory(store, config, create=True)
    target = work / "owner-signed.boc"
    target.write_bytes(source.read_bytes())
    target.chmod(0o600)
    before = _account(config, _manifest(store, config)["address"])
    _broadcast(config, "owner-signed.boc")
    return {
        "submitted": str(source),
        "nominator": _manifest(store, config)["address"],
        "balance_before_nanotokens": before.get("balance"),
        "note": (
            "Submission is not execution. Check nominator info for the "
            "effect; an expired or unauthorized message is simply ignored."
        ),
    }


def staking_state(store, config):
    """The balance and elector credit of the account that holds the stake.

    Shaped like the solo reader so the stake-mode arithmetic does not have to
    know which kind of account holds the principal.
    """
    _require_nominator(config)
    from . import scripts

    pool = nominator_address(config)
    contract = _account(config, pool)
    credit = scripts.read_elector_credit(store, config, pool)
    pending = scripts.read_wallet_pending(store, config, hot_wallet_address(config))
    return {
        "staking_account": pool,
        "staking_account_kind": "nominator contract",
        "wallet_balance_nanotokens": str(int(contract.get("balance") or 0)),
        "wallet_status": contract.get("acc_type"),
        "recoverable_nanotokens": credit.get("recoverable_nanotokens", "0"),
        "active_election_id": credit.get("active_election_id", "0"),
        "elector": credit.get("elector"),
        "elector_type": credit.get("elector_type"),
        # What the Safe has left unsigned, by the name nominator info gives it.
        "pending_wallet_transactions": pending.get("pending_transactions", []),
    }
