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
import shutil

from . import configuration, docker, process, storage

NANO = 10**9

# The node container already mounts the node's elections directory, so
# artifacts placed here are readable by its console without a new mount and
# without recreating a node that existed before this mode.
WORK_SUBDIRECTORY = "elections/nominator"
NODE_WORK_DIRECTORY = "/ever-node/elections/nominator"

# What the contract itself requires attached to an instruction, and what the
# reference deployment observed to be enough. Both are budgets at one set of
# network prices, not constants: deploy() estimates from the live config.
MINIMUM_OPERATION_FEE = 12 * 10**8
DEFAULT_OPERATION_FEE = 2 * NANO

OWNER_OPERATIONS = ("withdraw", "withdraw-all", "rotate")


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


def _run_tools(store, config, program, timeout=300):
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
        return json.loads(path.read_text())
    except ValueError as exc:
        raise process.OperationError("The prepared contract manifest is unreadable") from exc


def _pinned(store, config):
    result = _run_tools(store, config, "cat PINNED.json")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise process.OperationError(
            "The contract-tool image did not report its pinned versions"
        ) from exc


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
# Addresses here all start with '-1:'. The script-file form of node passes
# such an argument through, but 'node -e' reads it as one of node's own
# options, so the inline snippets below take their addresses from the
# environment instead of from argv.
node tools/cells.cjs compile /work/code.boc > /work/compile.json
export POOL=$(node tools/cells.cjs init /work/code.boc "$OWNER" "$VALIDATOR" /work/init.boc)
export FACTORY=$(node tools/cells.cjs factory /work/init.boc "$DEPLOY_VALUE" /work/factory-deploy.boc)
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

    The tag is a fingerprint of the build context, so an image built from
    different vendored sources is a different image rather than the same tag
    with different bytes. The build itself fails if the contract does not
    compile to its recorded code hash, so a usable image is evidence that the
    bytecode is the reviewed one.
    """
    staging = work_directory(store, config, create=True) / ".tools-build"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(mode=0o700)
    try:
        root = _stage_tool_sources(source, staging)
        tag = "local/evernode-nominator-tools:" + storage.calculate_directory_fingerprint(
            root
        )[:16]
        if docker.image_exists(tag):
            return tag, False
        shutil.copyfile(recipe, staging / "Dockerfile")
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
    """Locate the vendored contract, the way the kit's source is located.

    The contract and its tooling are vendored in this repository, not
    installed with the Python package, so a node records where its copy came
    from exactly as it records the orchestration scripts' source. A sibling
    of that recorded path is the documented checkout layout; anything else
    has to be named.
    """
    from pathlib import Path

    for candidate in (requested, config.get("nominator_source")):
        if candidate:
            path = Path(candidate).expanduser().absolute()
            if not (path / "PINNED.json").is_file():
                raise process.OperationError(
                    f"{path} does not look like the vendored single-nominator "
                    "source: it has no PINNED.json"
                )
            return path
    recorded = config.get("project_source")
    if recorded:
        sibling = Path(recorded).absolute().parent / "single-nominator"
        if (sibling / "PINNED.json").is_file():
            return sibling
    raise process.OperationError(
        "Cannot find the vendored single-nominator source; name it with "
        "--contract-source (in a standard checkout it is "
        "<repo>/vendor/single-nominator)"
    )


def prepare(store, config, *, source, recipe):
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
    config["nominator_source"] = str(source)
    work = work_directory(store, config, create=True)
    deploy_value = str(settings["NOMINATOR_DEPLOY_VALUE"])
    result = process.execute_command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{work}:/work",
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
        (work / name).chmod(0o600)
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
            "The contract does not exist yet. The address above is fixed by "
            "this code and this owner/wallet pair: a different pair is a "
            "different account, so changing either one means deploying again."
        ),
        "next": f"evernode nominator deploy -n {config['name']}",
    }


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
    funded = scripts.fund_nominator_deployer(
        store, config, wallet, factory, funding, budget
    )
    # The deployer is triggered by an unsigned external message: its code can
    # only send the message in its data, so nothing has to authorize it.
    _broadcast(config, "factory-deploy.boc")
    observed = _wait_for(
        config,
        pool,
        lambda item: item.get("acc_type") == "Active",
        what="nominator contract",
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

    Four things have to hold and none can be assumed from a successful
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
    wallet_state = scripts.read_wallet_pending_to(store, config, hot, pool)
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
    arguments = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        f"{work}:/work",
        "-w",
        "/opt/nominator",
        "-e",
        f"KIND={kind}",
        "-e",
        f"OUT={out}",
        "-e",
        f"ARGUMENT={argument}",
        config["nominator_tools_image"],
        OWNER_BODY_PROGRAM,
    ]
    result = process.execute_command(arguments, timeout=300)
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
    if not str(validator_address).startswith("-1:"):
        raise process.OperationError(
            "The hot wallet instructs the elector through this contract, so "
            "it must be a masterchain address starting with -1:"
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
            "consensus or ADNL keys. Update this node's recorded hot wallet "
            "afterwards, or it will keep signing with a wallet the contract "
            "no longer accepts."
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
    return {
        "staking_account": pool,
        "staking_account_kind": "nominator contract",
        "wallet_balance_nanotokens": str(int(contract.get("balance") or 0)),
        "wallet_status": contract.get("acc_type"),
        "recoverable_nanotokens": credit.get("recoverable_nanotokens", "0"),
        "active_election_id": credit.get("active_election_id", "0"),
        "elector": credit.get("elector"),
        "elector_type": credit.get("elector_type"),
        "pending_elector_transactions": [],
    }
