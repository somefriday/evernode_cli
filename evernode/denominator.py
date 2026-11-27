"""A validator that stakes through nominator pools many stakers deposit into.

A denominator validator runs one or two TON nominator-pool contracts (pinned
code 9a3ec14b..., upstream's, unchanged) in the masterchain. Stakers deposit
and withdraw from their own basechain wallets with the text comments 'd' and
'w'. The node's Safe is each pool's validator wallet, fixed in the pool's data
for life: it deposits the validator's own funds (op 4) and takes them out
(op 5), bids, and keeps the pools moving. A pool holds one stake at a time, so
a pair (A and B) bids in alternate elections and the node validates every round.

What keeps a pool moving is sent by the kit's denominator_cycle.sh, run by
cron: a validator-set update (op 6) after each new round while the stake is
out, the stake recovery once the pool allows it, and the payout of queued
withdrawals (op 2), which a pool needs before it bids again. The bid itself is
the kit's own bid script, unchanged, pointed at the pool whose turn it is.

Nothing in this module signs. The Safe signs through the kit's scripts; the
pool tools image builds and reads cells and never sees a key.
"""

import base64
import contextlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.parse

from . import configuration, docker, elections, nodes, process, scripts, storage, ui, wallets

NANO = 10**9
UNKNOWN = "unknown"
WORK_SUBDIRECTORY = "elections/denominator"
NODE_WORK_DIRECTORY = "/ever-node/elections/denominator"
SOURCE = configuration.VENDOR_DIRECTORY / "nominator-pool"
RECIPE = Path(__file__).resolve().parent / "assets" / "pool-tools.Dockerfile"
TOOL_SOURCES = ("contracts", "tools", "package.json", "package-lock.json", "PINNED.json")
CYCLE_SCRIPT = "denominator_cycle.sh"
LABELS = ("A", "B")
STAGES = ("denominator-prepared", "denominator-deployed", "denominator-verified")

# What node setup records for a denominator validator, and what prepare derives.
DEFAULTS = configuration.DENOMINATOR_SETTINGS
# A pool pays its stakers in the same transaction that takes the stake back from
# the elector. At 40 stakers that uses about 190,000 of the masterchain's
# 1,000,000 gas; from about 177 it runs out, and the whole stake is lost. The
# contract takes any limit it is deployed with, so this one is enforced here.
MAX_STAKERS = 40
# The floors upstream's deployment script refuses to go below.
MIN_STAKER_STAKE = 100
MIN_VALIDATOR_STAKE = 1000
# pool.fc's own figures.
RESERVE = 10 * NANO
DEPOSIT_FEE = 1 * NANO
# The validator funds a pool needs when the network has no config 40, as on
# Everscale mainnet (pool.fc's max_recommended_punishment_for_validator_misbehaviour).
PUNISHMENT_FLOOR = 101 * NANO

# A link opens an Oberton wallet transfer to a pool: t=p a transfer with a
# payload, da the destination, v the tokens and p the body as a URL-encoded
# base64 BOC. Every link but a stake attaches 2 tokens, which covers what any of
# them costs (at most 1.4 tokens, the payout of 15 queued withdrawals); the
# wallet app lets the sender change it, and what is not used comes back.
WALLET_LINK = "https://oberton.app/deeplink"
LINK_VALUE = 2
# Queued withdrawals one payout link pays: 15 fit the gas 2 tokens buy.
LINK_PAY_LIMIT = 15
# The bodies, with query id 0, as the pool tool builds them.
BODIES = {
    "stake": "te6ccgEBAQEABwAACgAAAABk",
    "withdraw": "te6ccgEBAQEABwAACgAAAAB3",
    "update": "te6ccgEBAQEADgAAGAAAAAYAAAAAAAAAAA==",
    "recover": "te6ccgEBAQEADgAAGEdldCQAAAAAAAAAAA==",
    "pay": "te6ccgEBAQEADwAAGgAAAAIAAAAAAAAAAA8=",
}
BOC_MAGIC = bytes.fromhex("b5ee9c72")


def require(config):
    actual = configuration.validator_type(config)
    if actual != "denominator":
        raise process.OperationError(
            f"This node is a {actual} validator; the denominator commands apply only to "
            "a validator that stakes through nominator pools"
        )


def settings(config):
    return dict(DEFAULTS, **(config.get("settings") or {}))


# ---- node setup --------------------------------------------------------------------------

# node setup's flags for the pools' terms, by the argument each fills.
SETUP_FLAGS = {
    "pools": "--pools",
    "reward_share": "--pool-reward-share",
    "max_stakers": "--pool-max-stakers",
    "min_staker_stake": "--pool-min-staker-stake",
    "min_validator_stake": "--pool-min-validator-stake",
}


def refuse_setup_flags(args, validator_type):
    """The pool settings mean nothing to another validator type."""
    given = [flag for name, flag in SETUP_FLAGS.items() if getattr(args, name, None) is not None]
    if given and validator_type != "denominator":
        raise process.OperationError(
            ", ".join(given) + " apply only to a denominator validator"
        )


def ask_settings(args):
    """The pools' terms, asked at node setup and fixed in each pool's data for life."""
    pools = int(
        ui.select_choice(
            None if args.pools is None else str(args.pools),
            "pool count",
            [
                ("2", "2 - a pair that bids in alternate elections, so the node validates every round"),
                ("1", "1 - one pool, which validates every other round"),
            ],
            flag="--pools",
            default="2",
        )
    )
    share = ui.prompt_integer(
        args.reward_share, "Validator's share of each reward, in percent", DEFAULTS["DENOMINATOR_REWARD_SHARE"]
    )
    max_stakers = ui.prompt_integer(
        args.max_stakers, f"Most stakers a pool takes (at most {MAX_STAKERS})", DEFAULTS["DENOMINATOR_MAX_STAKERS"]
    )
    min_staker = ui.prompt_integer(
        args.min_staker_stake, "Smallest staker deposit, in tokens", DEFAULTS["DENOMINATOR_MIN_STAKER_STAKE"]
    )
    min_validator = ui.prompt_integer(
        args.min_validator_stake,
        "Validator's own funds a pool needs before it bids, in tokens",
        DEFAULTS["DENOMINATOR_MIN_VALIDATOR_STAKE"],
    )
    return check_terms({
        "DENOMINATOR_POOLS": pools,
        "DENOMINATOR_REWARD_SHARE": share,
        "DENOMINATOR_MAX_STAKERS": max_stakers,
        "DENOMINATOR_MIN_STAKER_STAKE": min_staker,
        "DENOMINATOR_MIN_VALIDATOR_STAKE": min_validator,
        "DENOMINATOR_ADDRS": "",
        "DENOMINATOR_TOOLS_IMAGE": "",
    })


def check_terms(terms):
    """Refuse pool terms outside the safe range, at node setup and again before
    prepare derives addresses from them: a pool keeps its terms for life."""
    if terms["DENOMINATOR_POOLS"] not in range(1, len(LABELS) + 1):
        raise process.OperationError(f"A denominator validator runs 1 to {len(LABELS)} pools")
    if not 0 <= terms["DENOMINATOR_REWARD_SHARE"] <= 100:
        raise process.OperationError("The validator's reward share is a percentage from 0 to 100")
    if not 1 <= terms["DENOMINATOR_MAX_STAKERS"] <= MAX_STAKERS:
        raise process.OperationError(
            f"A pool takes 1 to {MAX_STAKERS} stakers: it pays all of them in one "
            "transaction, which must fit the masterchain gas limit"
        )
    if terms["DENOMINATOR_MIN_STAKER_STAKE"] < MIN_STAKER_STAKE:
        raise process.OperationError(f"The smallest staker deposit is at least {MIN_STAKER_STAKE} tokens")
    if terms["DENOMINATOR_MIN_VALIDATOR_STAKE"] < MIN_VALIDATOR_STAKE:
        raise process.OperationError(
            f"The validator's own funds in each pool are at least {MIN_VALIDATOR_STAKE} tokens"
        )
    return terms


# ---- pools -------------------------------------------------------------------------------

def pools(config, label=None):
    """This node's pools, from its prepared manifest; one when LABEL names it."""
    recorded = (config.get("denominator") or {}).get("pools") or []
    if not recorded:
        raise process.OperationError(
            f"This node has no pools yet; run: evernode denominator prepare -n {config['name']}"
        )
    if label is None:
        return recorded
    chosen = [item for item in recorded if item["label"] == label]
    if not chosen:
        raise process.OperationError(
            f"This node has no pool {label}; its pools are "
            + ", ".join(item["label"] for item in recorded)
        )
    return chosen


def safe_address(config):
    """The Safe every pool takes instructions from: this node's own wallet."""
    address = (wallets.node_wallet_profile(config) or {}).get("wallet_address")
    if not address:
        raise process.OperationError(
            "This node has no wallet yet, and the pools' addresses depend on it; run: "
            f"evernode wallet create -n {config['name']}"
        )
    if not str(address).startswith("-1:"):
        raise process.OperationError(
            f"A pool's validator wallet must be in the masterchain; {address} is not"
        )
    return str(address).lower()


def work_directory(store, config, create=False):
    path = store.node_directory(config["name"]) / WORK_SUBDIRECTORY
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() or path.is_symlink():
        storage.validate_private_path(path)
    return path


def _console(config, command, timeout=60):
    container = configuration.node_container_name(config)
    obj = docker.inspect_container(container)
    if not obj or obj["State"]["Status"] != "running":
        raise process.OperationError(
            "The node container is not running, and its console is the only way to "
            "reach the chain; start it first"
        )
    return process.execute_command(
        ["docker", "exec", container, "console", "-C", "/ever-node/node_cfg/console.json", "-jc", command],
        timeout=timeout,
    )


def _account(config, address):
    result = _console(config, f"getaccount {address}")
    try:
        value = json.loads(result.stdout)
    except ValueError:
        return {"acc_type": "Unknown", "balance": "0"}
    return value if isinstance(value, dict) else {"acc_type": "Unknown", "balance": "0"}


def _tools(store, config, program, environment=None, timeout=300, image=None):
    """Run a shell program in the pool tools image, which sees only this node's pool directory."""
    image = image or settings(config)["DENOMINATOR_TOOLS_IMAGE"]
    if not image:
        raise process.OperationError(
            f"This node has no pool tools image recorded; run: evernode denominator prepare -n {config['name']}"
        )
    work = work_directory(store, config, create=True)
    return process.execute_command(
        [
            "docker", "run", "--rm", "--network", "none", "-v", f"{work}:/work", "-w", "/opt/nominator",
            *(part for name, value in (environment or {}).items() for part in ("-e", f"{name}={value}")),
            image, program,
        ],
        timeout=timeout,
    )


def read_pool(store, config, address):
    """One pool's account, decoded by the pool tool from the state the node hands over."""
    # Not the cycle's file name: a scheduled run may be reading the same pool.
    name = f"read_{address.split(':', 1)[1]}.boc"
    work = work_directory(store, config, create=True)
    (work / name).unlink(missing_ok=True)
    _console(config, f"getaccountstate {address} {NODE_WORK_DIRECTORY}/{name}")
    result = _tools(store, config, f"node tools/cells.cjs account /work/{name}")
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise process.OperationError(f"Could not read pool {address}") from exc


def require_tools_image(config):
    image = settings(config)["DENOMINATOR_TOOLS_IMAGE"]
    if not image or not docker.image_exists(image):
        raise process.OperationError(
            f"The pool tools image {image or ''} is not on this host, so the pools can be "
            f"neither read nor bid from; run: evernode denominator prepare -n {config['name']}"
        )


# ---- prepare, deploy, verify ------------------------------------------------------------

def _stage_tool_sources(source, staging):
    root = staging / "src"
    root.mkdir(parents=True)
    missing = [name for name in TOOL_SOURCES if not (source / name).exists()]
    if missing:
        raise process.OperationError(
            f"{source} is missing " + ", ".join(missing) + "; it does not look like the vendored nominator-pool source"
        )
    for name in TOOL_SOURCES:
        item = source / name
        if item.is_dir():
            shutil.copytree(item, root / name, symlinks=False)
        else:
            shutil.copyfile(item, root / name)


def build_tools_image(store, config, source, recipe=RECIPE, timeout=1800):
    """Build the pinned pool tools image, or reuse it: its tag fingerprints the build context."""
    staging = work_directory(store, config, create=True) / ".tools-build"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(mode=0o700)
    try:
        _stage_tool_sources(source, staging)
        shutil.copyfile(recipe, staging / "Dockerfile")
        tag = "local/evernode-pool-tools:" + storage.calculate_directory_fingerprint(staging)[:16]
        if docker.image_exists(tag):
            return tag, False
        process.execute_command(
            ["docker", "build", "--tag", tag, "--file", str(staging / "Dockerfile"), str(staging)],
            timeout=timeout,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return tag, True


def resolve_contract_source(requested=None):
    if requested:
        path = Path(requested).expanduser().absolute()
        if not (path / "PINNED.json").is_file():
            raise process.OperationError(f"{path} does not look like the vendored nominator-pool source")
        return path
    if not (SOURCE / "PINNED.json").is_file():
        raise process.OperationError(
            f"evernode's nominator-pool source is missing from {SOURCE}: reinstall evernode"
        )
    return SOURCE


PREPARE_PROGRAM = r"""set -e
# Addresses start with '-1:', which 'node -e' would read as its own option, so
# the inline snippets take them from the environment.
node tools/cells.cjs compile /work/code.boc > /work/compile.json
rm -f /work/pools.txt
salt=0
while [ "$salt" -lt "$POOLS" ]; do
  ADDRESS=$(node tools/cells.cjs init /work/code.boc "$VALIDATOR" "$SHARE" "$MAX_STAKERS" "$MIN_VALIDATOR" "$MIN_STAKER" "$salt" "/work/init-$salt.boc")
  FACTORY=$(node tools/cells.cjs factory "/work/init-$salt.boc" "$DEPLOY_VALUE" "/work/factory-$salt.boc")
  echo "$salt $ADDRESS $FACTORY" >> /work/pools.txt
  salt=$((salt + 1))
done
node -e '
  const fs = require("fs");
  const pinned = require("/opt/nominator/PINNED.json");
  const compiled = JSON.parse(fs.readFileSync("/work/compile.json", "utf8"));
  if (compiled.codeHash !== pinned.codeHash) throw Error("compiled " + compiled.codeHash + " but pinned " + pinned.codeHash);
  const pools = fs.readFileSync("/work/pools.txt", "utf8").trim().split("\n").map(line => {
    const [salt, address, factory] = line.split(" ");
    return {salt: Number(salt), address, factory};
  });
  fs.writeFileSync("/work/manifest.json", JSON.stringify({
    upstreamRepository: pinned.upstreamRepository, upstreamCommit: pinned.upstreamCommit,
    codeHash: compiled.codeHash, compiler: pinned.compiler, validator: process.env.VALIDATOR,
    rewardShare: Number(process.env.SHARE), maxStakers: Number(process.env.MAX_STAKERS),
    minValidatorStake: process.env.MIN_VALIDATOR, minStakerStake: process.env.MIN_STAKER,
    deployValueNanotokens: process.env.DEPLOY_VALUE, pools,
  }, null, 2) + "\n");
'
cat /work/manifest.json
"""


def _manifest(store, config):
    path = work_directory(store, config) / "manifest.json"
    if not path.is_file():
        raise process.OperationError(
            f"This node has no prepared pools; run: evernode denominator prepare -n {config['name']}"
        )
    try:
        manifest = json.loads(path.read_text())
    except ValueError as exc:
        raise process.OperationError("The prepared pools' manifest is unreadable") from exc
    recorded = [item["address"] for item in (config.get("denominator") or {}).get("pools") or []]
    if recorded and [item["address"] for item in manifest.get("pools") or []] != recorded:
        raise process.OperationError(
            "The prepared pool files are for other pools than this node's; run: evernode "
            f"denominator prepare -n {config['name']}"
        )
    return manifest


def prepare(store, config, *, source=None, new_contract=False, force=False):
    """Compile the pool contract and derive the pools' addresses. Nothing is sent or spent.

    A pool's address follows from the code, the Safe and the terms node setup
    recorded, so all of them are settled before anything exists on chain. A
    prepare that drops a recorded pool first reads what the pool still holds,
    and asks before dropping one that holds anything (_check_dropped_pools).
    The question comes before the election lock: a scheduled run that finds the
    lock taken skips, so only the change itself holds it.
    """
    require(config)
    validator = safe_address(config)
    terms = check_terms(settings(config))
    source = resolve_contract_source(source)
    tag, built = build_tools_image(store, config, source)
    work = work_directory(store, config, create=True)
    staging = work / ".prepare"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(mode=0o700)
    try:
        result = process.execute_command(
            [
                "docker", "run", "--rm", "--network", "none", "-v", f"{staging}:/work",
                "-e", f"VALIDATOR={validator}",
                "-e", f"POOLS={int(terms['DENOMINATOR_POOLS'])}",
                "-e", f"SHARE={int(terms['DENOMINATOR_REWARD_SHARE']) * 100}",
                "-e", f"MAX_STAKERS={int(terms['DENOMINATOR_MAX_STAKERS'])}",
                "-e", f"MIN_VALIDATOR={int(terms['DENOMINATOR_MIN_VALIDATOR_STAKE']) * NANO}",
                "-e", f"MIN_STAKER={int(terms['DENOMINATOR_MIN_STAKER_STAKE']) * NANO}",
                "-e", f"DEPLOY_VALUE={int(terms['DENOMINATOR_DEPLOY_VALUE'])}",
                "-w", "/opt/nominator", tag, PREPARE_PROGRAM,
            ],
            timeout=600,
        )
        try:
            manifest = json.loads(result.stdout[result.stdout.index("{"):])
        except (ValueError, IndexError) as exc:
            raise process.OperationError("Pool preparation did not produce a manifest") from exc
        if int(manifest["maxStakers"]) > MAX_STAKERS:
            raise process.OperationError(f"A pool takes at most {MAX_STAKERS} stakers")
        addresses = [item["address"] for item in manifest["pools"]]
        recorded = (config.get("denominator") or {}).get("pools") or []
        deployed = [item["address"] for item in recorded]
        on_chain = config.get("setup_stage") in STAGES[1:] + ("ready-for-elections",)
        if deployed and on_chain and addresses != deployed and not new_contract:
            raise process.OperationError(
                f"This node's pools are deployed at {', '.join(deployed)}, and this code gives "
                f"{', '.join(addresses)}. Prepare them from the source they were built from, or use "
                "--new-contract to move the node to the new pools"
            )
        dropped = _check_dropped_pools(
            store, config, [item for item in recorded if item["address"] not in addresses], on_chain, force
        )
        with elections.hold_election_lock(store, config["name"]):
            for item in staging.iterdir():
                item.chmod(0o600)
                os.replace(item, work / item.name)
            terms["DENOMINATOR_ADDRS"] = " ".join(addresses)
            terms["DENOMINATOR_TOOLS_IMAGE"] = tag
            config["settings"] = dict(config.get("settings") or {}, **{
                key: terms[key] for key in ("DENOMINATOR_ADDRS", "DENOMINATOR_TOOLS_IMAGE")
            })
            config["denominator"] = dict(
                config.get("denominator") or {},
                pools=[
                    {"label": LABELS[item["salt"]], "address": item["address"], "factory": item["factory"],
                     "salt": item["salt"]}
                    for item in manifest["pools"]
                ],
                code_hash=manifest["codeHash"],
                upstream_commit=manifest["upstreamCommit"],
            )
            configuration.write_node_environment(
                store.node_directory(config["name"]) / "scripts" / "env.sh",
                configuration.build_node_environment(config),
            )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if deployed and on_chain and any(address not in deployed for address in addresses):
        # A pool that does not exist yet takes deploy, verify and deposit again;
        # until then election start refuses.
        config["setup_stage"] = "denominator-prepared"
    if config.get("setup_stage") in ("wallet-deployed", "imported", "denominator-prepared"):
        config["setup_stage"] = "denominator-prepared"
    result = {
        "tools_image": tag,
        "tools_image_built": built,
        "contract_source": str(source),
        "code_hash": manifest["codeHash"],
        "pools": config["denominator"]["pools"],
        "terms": {
            "reward_share_percent": terms["DENOMINATOR_REWARD_SHARE"],
            "max_stakers": terms["DENOMINATOR_MAX_STAKERS"],
            "min_staker_stake_tokens": terms["DENOMINATOR_MIN_STAKER_STAKE"],
            "min_validator_stake_tokens": terms["DENOMINATOR_MIN_VALIDATOR_STAKE"],
        },
        "note": (
            "Each address is fixed by this code, this node's Safe and these terms: a pool "
            "deployed at it keeps them for life."
        ),
        "next": wallets.next_setup_command(config) or f"evernode denominator info -n {config['name']}",
    }
    if dropped:
        # The only record left of them: evernode no longer reads or maintains these.
        result["dropped_pools"] = dropped
    return config, result


def _tokens(nanotokens):
    """Exact tokens for a person to read: 13797964098 reads 13.797964098."""
    whole, fraction = divmod(int(nanotokens), NANO)
    return f"{whole:,}" + (f".{fraction:09d}".rstrip("0") if fraction else "")


def _pool_holdings(store, config, item, pending):
    """What one pool holds: its balance, its stake at the elector, its stakers and
    queue, and the Safe's unsigned transactions to it (PENDING, or UNKNOWN).

    An address that never existed holds nothing and needs no tools image. The
    rest is read with the tools image the node records, which matches the code
    of the pools it records.
    """
    address = item["address"]
    unsigned = pending if pending == UNKNOWN else sum(
        1 for transaction in pending if str(transaction.get("dest", "")).lower() == address.lower()
    )
    holdings = {"pool": item["label"], "address": address, "balance_nanotokens": "0",
                "at_elector_nanotokens": "0", "stakers": 0, "queued_withdrawals": 0,
                "unsigned_safe_transactions": unsigned}
    account = _account(config, address)
    kind = account.get("acc_type")
    if kind == "Nonexist":
        return holdings
    if kind not in ("Active", "Uninit", "Frozen"):
        raise process.OperationError("the node did not return its account")
    pool = read_pool(store, config, address).get("pool") or {}
    credit = int(scripts.read_elector_credit(store, config, address)["recoverable_nanotokens"])
    sent = int(pool.get("stakeAmountSent") or 0) if pool.get("state") else 0
    holdings.update(
        balance_nanotokens=str(int(account.get("balance") or 0)),
        at_elector_nanotokens=str(max(credit, sent)),
        stakers=int(pool.get("nominatorsCount") or 0),
        queued_withdrawals=int(pool.get("withdrawRequests") or 0),
    )
    return holdings


def _holds_something(holdings):
    return bool(
        int(holdings["balance_nanotokens"]) or int(holdings["at_elector_nanotokens"])
        or holdings["stakers"] or holdings["queued_withdrawals"]
        or holdings["unsigned_safe_transactions"] != 0
    )


def _dropping_question(held):
    lines = [
        f"Pool {entry['pool']} ({entry['address']}) still holds {_tokens(entry['balance_nanotokens'])} tokens "
        f"in the pool and {_tokens(entry['at_elector_nanotokens'])} at the elector; it has {entry['stakers']} "
        f"staker(s) and {entry['queued_withdrawals']} queued withdrawal(s), and the Safe holds "
        f"{entry['unsigned_safe_transactions']} unsigned transaction(s) to it."
        for entry in held
    ]
    lines.append(
        "Once dropped, evernode no longer maintains " + ("it" if len(held) == 1 else "them")
        + ": nothing counts rounds, recovers a stake or pays a withdrawal there, and the validator's "
        "funds there are out of evernode's reach."
    )
    lines.append("Drop " + ("this pool" if len(held) == 1 else "these pools") + " from this node for good?")
    return "\n".join(lines)


def _check_dropped_pools(store, config, dropped, on_chain, force):
    """Read what each pool that prepare is about to drop still holds, and ask
    before dropping any that holds something. Returns what was read.

    Nothing maintains a dropped pool: no rounds are counted, no stake recovered,
    no withdrawal paid, and the validator's funds in it are out of evernode's
    reach. Only --force answers the question; --yes does not, so a habitual
    --yes cannot drop funds. A pool that cannot be read blocks the change once
    the pools are deployed, unless --force; before that, prepare works without
    the node and reports such a pool as not checked.
    """
    if not dropped:
        return []
    pending = _reading(
        lambda: scripts.read_wallet_pending(store, config, safe_address(config)).get("pending_transactions", [])
    )
    checked, unread = [], []
    for item in dropped:
        try:
            checked.append(_pool_holdings(store, config, item, pending))
        except (process.OperationError, KeyError, TypeError, ValueError) as exc:
            unread.append({"pool": item["label"], "address": item["address"], "not_checked": str(exc)})
    if unread and on_chain and not force:
        raise process.OperationError(
            "Nothing was changed: "
            + "; ".join(f"pool {entry['pool']} ({entry['address']}) could not be read: {entry['not_checked']}"
                        for entry in unread)
            + ". Rerun once the node runs and its pools can be read, or pass --force to drop them unread"
        )
    held = [entry for entry in checked if _holds_something(entry)]
    if held and not force:
        question = _dropping_question(held)
        if not sys.stdin.isatty():
            raise process.OperationError(f"{question} Nothing was changed; --force drops them without asking")
        ui.confirm_operation(False, question, flag="--force")
    return checked + unread


def _wait_active(config, address, attempts=15, delay=4):
    for attempt in range(attempts):
        if _account(config, address).get("acc_type") == "Active":
            return True
        if attempt + 1 < attempts:
            time.sleep(delay)
    return False


def deploy(store, config):
    """Fund each pool's one-shot deployer from the Safe and trigger it."""
    require(config)
    _manifest(store, config)
    terms = settings(config)
    wallet = safe_address(config)
    budget = int(terms["DENOMINATOR_DEPLOY_VALUE"])
    funding = budget + int(terms["DENOMINATOR_DEPLOY_MARGIN"])
    results = []
    for item in pools(config):
        if _account(config, item["address"]).get("acc_type") == "Active":
            results.append({"pool": item["label"], "address": item["address"], "result": "already deployed"})
            continue
        funded = scripts.fund_nominator_deployer(store, config, wallet, item["factory"], funding, budget)
        active = False
        for _ in range(3):
            _console(config, f"sendmessage {NODE_WORK_DIRECTORY}/factory-{item['salt']}.boc")
            if _wait_active(config, item["address"]):
                active = True
                break
        if not active:
            raise process.OperationError(
                f"Pool {item['label']} ({item['address']}) did not appear after three sends of its "
                f"deployer's trigger. The deployer is paid; run evernode denominator deploy -n "
                f"{config['name']} again later"
            )
        results.append({"pool": item["label"], "address": item["address"], "result": "deployed",
                        "deployer_funding": funded})
    if config.get("setup_stage") == "denominator-prepared":
        config["setup_stage"] = "denominator-deployed"
    return config, {"pools": results, "next": wallets.next_setup_command(config)}


def verify(store, config):
    """Check each deployed pool's code, validator wallet and terms against what was prepared."""
    require(config)
    manifest = _manifest(store, config)
    wallet = safe_address(config)
    checked = []
    for item in pools(config):
        account = read_pool(store, config, item["address"])
        problems = []
        if account.get("state") != "active":
            problems.append(f"the account is {account.get('state')}, not active")
        if account.get("codeHash") != manifest["codeHash"]:
            problems.append(f"its code hash is {account.get('codeHash')}, not {manifest['codeHash']}")
        pool = account.get("pool") or {}
        terms = pool.get("config") or {}
        expected = {
            "validator": wallet,
            "rewardShare": int(manifest["rewardShare"]),
            "maxNominators": int(manifest["maxStakers"]),
            "minValidatorStake": str(manifest["minValidatorStake"]),
            "minNominatorStake": str(manifest["minStakerStake"]),
        }
        for field, value in expected.items():
            if terms.get(field) != value:
                problems.append(f"its {field} is {terms.get(field)}, not {value}")
        if int(terms.get("maxNominators") or 0) > MAX_STAKERS:
            problems.append(f"it takes {terms.get('maxNominators')} stakers, more than {MAX_STAKERS}")
        if problems:
            raise process.OperationError(
                f"Pool {item['label']} ({item['address']}) is not the one that was prepared: " + "; ".join(problems)
            )
        checked.append({"pool": item["label"], "address": item["address"], "code_hash": account["codeHash"],
                        "validator": terms["validator"], "balance_nanotokens": account.get("balance")})
    if config.get("setup_stage") in ("denominator-prepared", "denominator-deployed"):
        config["setup_stage"] = "denominator-verified"
    return config, {"verified": True, "pools": checked, "next": wallets.next_setup_command(config)}


# ---- the validator's own funds -------------------------------------------------------------

def _cycle(store, config, *arguments, timeout):
    """Run the kit's denominator_cycle.sh and return the JSON line it ends with."""
    result = scripts.execute_validator_script(
        store, config, CYCLE_SCRIPT, *arguments, timeout=timeout, sensitive=True
    )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise process.OperationError(f"Could not read the result of {CYCLE_SCRIPT} {arguments[0]}") from exc


def _send(store, config, address, value, kind, argument=None):
    """One message from the Safe to one of this node's pools."""
    extra = () if argument is None else (str(argument),)
    return _cycle(store, config, "send", address, str(value), kind, *extra, timeout=1500)


def required_validator_funds(config):
    floor = int(settings(config)["DENOMINATOR_MIN_VALIDATOR_STAKE"]) * NANO
    return max(floor, PUNISHMENT_FLOOR)


def _funded(store, config):
    need = required_validator_funds(config)
    return all(
        int((read_pool(store, config, item["address"]).get("pool") or {}).get("validatorAmount") or 0) >= need
        for item in pools(config)
    )


def deposit(store, config, *, amount, label=None):
    """Send the validator's own funds from the Safe into each pool, or into LABEL (op 4)."""
    require(config)
    if amount <= 0:
        raise process.OperationError("A deposit is a positive number of tokens")
    if config.get("setup_stage") not in ("denominator-verified", "ready-for-elections"):
        raise process.OperationError(
            "The pools have to be deployed and verified first; run: "
            + (wallets.next_setup_command(config) or f"evernode denominator verify -n {config['name']}")
        )
    sent = []
    for item in pools(config, label):
        # The pool keeps 1 token of every deposit for the work it does.
        result = _send(store, config, item["address"], (amount * NANO) + DEPOSIT_FEE, "deposit-validator")
        pool = read_pool(store, config, item["address"]).get("pool") or {}
        sent.append({"pool": item["label"], "address": item["address"], "deposited_tokens": amount,
                     "validator_funds_nanotokens": pool.get("validatorAmount"), "safe": result})
    if config.get("setup_stage") == "denominator-verified" and _funded(store, config):
        config["setup_stage"] = "ready-for-elections"
    return config, {
        "deposits": sent,
        "validator_funds_required_nanotokens": str(required_validator_funds(config)),
        "stage": config.get("setup_stage"),
        "next": wallets.next_setup_command(config) or f"evernode denominator info -n {config['name']}",
    }


def withdraw(store, config, *, amount, label):
    """Take the validator's own funds out of one idle pool, to the Safe (op 5)."""
    require(config)
    if amount <= 0:
        raise process.OperationError("A withdrawal is a positive number of tokens")
    item = pools(config, label)[0]
    account = read_pool(store, config, item["address"])
    pool = account.get("pool") or {}
    if pool.get("state") != 0:
        raise process.OperationError(
            f"Pool {item['label']}'s stake is out; the validator's funds can be withdrawn only "
            "between its rounds, once the stake is back"
        )
    free = int(account["balance"]) - RESERVE - int(pool["nominatorsTotal"])
    if amount * NANO > free:
        raise process.OperationError(
            f"Pool {item['label']} holds {max(free, 0) // NANO} tokens beyond what its stakers own and "
            "its 10-token reserve; that is the most the validator can withdraw now"
        )
    result = _send(store, config, item["address"], NANO, "withdraw-validator", amount * NANO)
    return config, {"pool": item["label"], "address": item["address"], "withdrawn_tokens": amount,
                    "to": safe_address(config), "safe": result}


# ---- what the pools hold ------------------------------------------------------------------

def _phase(pool, credit):
    """idle, bid sent or staked, as the pool records it; returned once the
    elector holds the whole stake for the pool again: it was not elected, or its
    round and hold time are over. The pool takes it back only after two new
    validator sets and the hold time either way."""
    if pool["state"] != 0 and isinstance(credit, int) and credit >= int(pool["stakeAmountSent"]) > 0:
        return "returned"
    return {0: "idle", 1: "bid sent", 2: "staked"}.get(pool["state"], UNKNOWN)


def _reading(read):
    try:
        return read()
    except (process.OperationError, KeyError, TypeError, ValueError):
        return UNKNOWN


def _next_action(config, pool, credit, p34_since, now):
    """What the kit's cycle does next for this pool, as denominator_cycle.sh decides it."""
    if pool["state"] == 0:
        if pool["withdrawRequests"]:
            return f"pay {pool['withdrawRequests']} queued withdrawal(s)"
        if not bidding(config):
            return "nothing: bidding is stopped"
        return "bid in the next election it can"
    changes, changed, held = pool["validatorSetChanges"], pool["validatorSetChangeTime"], pool["stakeHeldFor"]
    if changes < 3 and isinstance(p34_since, int) and p34_since > changed:
        return "count the new validator set (op 6)"
    if changes < 2:
        return f"wait for {2 - changes} more new validator set(s)"
    if changes < 3 and now - changed <= held + 60:
        return "recover the stake after " + time.strftime(
            "%Y-%m-%d %H:%M:%SZ", time.gmtime(changed + held + 61)
        )
    if isinstance(credit, int) and credit > 0 and (credit >= int(pool["stakeAmountSent"]) or changes > 2):
        return "recover the stake"
    return "wait for the elector to release the stake"


def _pool_report(store, config, item, p34_since, now):
    account = read_pool(store, config, item["address"])
    pool = account.get("pool")
    if not pool:
        return {"pool": item["label"], "address": item["address"], "state": account.get("state"),
                "error": "not the pinned pool contract, or not deployed"}
    credit = _reading(lambda: int(scripts.read_elector_credit(store, config, item["address"])["recoverable_nanotokens"]))
    balance = int(account["balance"])
    report = {
        "pool": item["label"],
        "address": item["address"],
        "phase": _phase(pool, credit),
        "balance_nanotokens": str(balance),
        "stakers": pool["nominatorsCount"],
        "max_stakers": pool["config"]["maxNominators"],
        "stakers_funds_nanotokens": pool["nominatorsTotal"],
        "pending_deposits_nanotokens": pool["pendingTotal"],
        "queued_withdrawals": pool["withdrawRequests"],
        "validator_funds_nanotokens": pool["validatorAmount"],
        "validator_funds_required_nanotokens": str(required_validator_funds(config)),
        "stake_sent_nanotokens": pool["stakeAmountSent"],
        # Until a pool's first bid this holds its salt (0 or 1), which only sets its address apart.
        "election_id": pool["stakeAt"] if pool["stakeAt"] >= len(LABELS) else None,
        "validator_sets_counted": pool["validatorSetChanges"],
        "recoverable_nanotokens": str(credit) if isinstance(credit, int) else credit,
        "reward_share_percent": pool["config"]["rewardShare"] / 100,
        "min_staker_stake_nanotokens": pool["config"]["minNominatorStake"],
        "next_action": _next_action(config, pool, credit, p34_since, now),
    }
    if pool["state"] == 0:
        report["validator_withdrawable_nanotokens"] = str(max(0, balance - RESERVE - int(pool["nominatorsTotal"])))
    return report


def info(store, config, *, label=None):
    """Where each pool's money is and what the cycle does next."""
    require(config)
    now = int(time.time())
    p34_since = _reading(lambda: int(nodes.fetch_chain_parameter(config, 34)["utime_since"]))
    safe = safe_address(config)
    wallet = _account(config, safe)
    pending = _reading(lambda: scripts.read_wallet_pending(store, config, safe).get("pending_transactions", []))
    return {
        "node": config["name"],
        "safe": safe,
        "safe_balance_nanotokens": str(int(wallet.get("balance") or 0)),
        "pending_safe_transactions": pending,
        "bidding": bidding(config),
        "pools": [_pool_report(store, config, item, p34_since, now) for item in pools(config, label)],
        "note": (
            "A pool's stake is at the elector from its bid until the hold time after its round "
            "ends (9 h 6 min on mainnet); stakers' withdrawals asked for meanwhile are paid when it returns."
        ),
    }


def stakers(store, config, *, label=None):
    require(config)
    listed = []
    for item in pools(config, label):
        pool = read_pool(store, config, item["address"]).get("pool") or {}
        listed.append({"pool": item["label"], "address": item["address"], "stakers": pool.get("nominators", [])})
    return {"node": config["name"], "pools": listed}


def stake_status(store, config):
    """evernode stake status, for a denominator validator."""
    return info(store, config)


# ---- links ----------------------------------------------------------------------------------

def _single_cell_boc(data):
    """A BOC of one cell holding DATA (whole bytes, no references), as the pool tool writes it."""
    if len(data) > 127:
        raise process.OperationError("A link body holds at most 127 bytes")
    cell = bytes([0, 2 * len(data)]) + data
    return base64.b64encode(
        BOC_MAGIC + bytes([0x01, 0x01, 0x01, 0x01, 0x00, len(cell), 0x00]) + cell
    ).decode()


def _pay_one_body(staker):
    workchain, _, digits = str(staker).partition(":")
    if workchain != "0" or len(digits) != 64:
        raise process.OperationError("A staker's address is in the basechain: 0: and 64 hex digits")
    try:
        account = bytes.fromhex(digits)
    except ValueError as exc:
        raise process.OperationError("A staker's address is 0: and 64 hex digits") from exc
    return _single_cell_boc((3).to_bytes(4, "big") + bytes(8) + account)


def _link(address, value, body):
    return f"{WALLET_LINK}?t=p&da={address}&v={value}&p={urllib.parse.quote(body, safe='')}"


def links(config, *, label=None, staker=None):
    """Every wallet link for each pool, for stakers to open in Oberton. Offline: nothing is read or sent."""
    require(config)
    stake_value = int(settings(config)["DENOMINATOR_MIN_STAKER_STAKE"]) + DEPOSIT_FEE // NANO
    kinds = [
        ("stake", stake_value, BODIES["stake"],
         "Deposit. The tokens minus the pool's 1-token fee join the stake; at least the minimum deposit."),
        ("withdraw", LINK_VALUE, BODIES["withdraw"],
         "Withdraw everything. Paid at once while the pool is idle, otherwise when its stake returns."),
        ("update", LINK_VALUE, BODIES["update"],
         "Rescue: count a new validator set; once in each of the two rounds after the bid."),
        ("recover", LINK_VALUE, BODIES["recover"],
         "Rescue: ask the elector for the stake, 9 h 7 min after the second update on mainnet."),
        ("pay", LINK_VALUE, BODIES["pay"],
         f"Rescue: pay up to {LINK_PAY_LIMIT} queued withdrawals; press again until none wait."),
    ]
    if staker is not None:
        kinds.append(("pay-one", LINK_VALUE, _pay_one_body(staker),
                      f"Rescue: pay the queued withdrawal of {staker} alone."))
    result = []
    for item in pools(config, label):
        result.append({
            "pool": item["label"],
            "address": item["address"],
            "links": [
                {"kind": kind, "attach_tokens": value, "body_base64": body, "what_it_does": what,
                 "link": _link(item["address"], value, body)}
                for kind, value, body, what in kinds
            ],
        })
    return {
        "node": config["name"],
        "pools": result,
        "how_to_use": (
            "Open a link, or a QR code of it, in Oberton and confirm; the app lets the sender "
            "change the tokens. Stakers use a basechain (0:) wallet. Oberton sends without "
            "bounce: a deposit the pool refuses (full, below the minimum) stays in it. The "
            "rescue links are for when this validator stops; anyone may send them."
        ),
    }


# ---- elections ------------------------------------------------------------------------------

def is_denominator(config):
    """Whether node setup made this node a denominator validator."""
    return configuration.has_role(config) and configuration.validator_type(config) == "denominator"


def bidding(config):
    return elections.denominator_bidding(config)


def require_ready(config):
    """What election start needs: funded, verified pools and their tools image."""
    require(config)
    if config.get("setup_stage") != "ready-for-elections":
        raise process.OperationError(
            "Complete the pools' setup before enabling elections; run: "
            + (wallets.next_setup_command(config) or f"evernode denominator info -n {config['name']}")
        )
    require_tools_image(config)


def start_bidding(store, config):
    """election start: install the cycle, or switch a maintenance-only one back to bidding."""
    require_ready(config)
    config["denominator"] = dict(config.get("denominator") or {}, bidding=True)
    if elections.node_election_cron_path(config["name"]).exists():
        elections.write_election_programs(store, config)
    else:
        elections.enable_node_election_schedule(store, config)
    return config


def stop_bidding(store, config):
    """election stop: the cycle keeps running without bids, so every stake still comes back."""
    return elections.stop_denominator_bidding(store, config)


def maintain(store, config):
    """evernode stake recover: run the pools' maintenance now (updates, recovery, payouts)."""
    require(config)
    require_tools_image(config)
    summary = _cycle(store, config, "cycle", "maintain", timeout=3600)
    return dict(summary, note=f"Check the result with evernode denominator info -n {config['name']}")


def retire(store, config):
    """Remove the cycle once no pool has a stake out, the elector owes none of them
    anything, and the Safe holds no unsigned transaction to any of them: a bid
    signed after the cycle is gone would send a stake out that nothing brings back."""
    require(config)
    if bidding(config):
        raise process.OperationError(
            f"{config['name']} still bids; stop it first: evernode election stop -n {config['name']}"
        )
    blocking = []
    pending = scripts.read_wallet_pending(store, config, safe_address(config)).get("pending_transactions") or []
    for item in pools(config):
        waiting = [str(transaction.get("id")) for transaction in pending
                   if str(transaction.get("dest", "")).lower() == item["address"].lower()]
        if waiting:
            blocking.append(
                f"the Safe holds {len(waiting)} unsigned transaction(s) to pool {item['label']} (id "
                f"{', '.join(waiting)}): sign them with evernode stake confirm -n {config['name']} "
                "--transaction-id ID, or let them expire"
            )
        pool = read_pool(store, config, item["address"]).get("pool") or {}
        credit = int(scripts.read_elector_credit(store, config, item["address"]).get("recoverable_nanotokens") or 0)
        if pool.get("state") != 0:
            blocking.append(f"pool {item['label']}'s stake is out")
        elif credit:
            blocking.append(f"the elector still owes pool {item['label']} {credit // NANO} tokens")
        elif pool.get("withdrawRequests"):
            blocking.append(f"pool {item['label']} has {pool['withdrawRequests']} queued withdrawal(s) to pay")
    if blocking:
        raise process.OperationError(
            "The cycle is still needed: " + "; ".join(blocking) + ". Leave it running and retire later"
        )
    elections.disable_node_election_schedule(config["name"])
    return config, {"node": config["name"], "cycle": "removed",
                    "note": "Stakers can still withdraw from an idle pool at any time with the withdraw link."}


# ---- command line ---------------------------------------------------------------------------

OFFLINE = ("prepare", "link")
READ_ONLY = ("info", "stakers", "link", "verify")
# prepare takes the election lock itself, after any question it asks.
ELECTION_LOCKED = ("deploy", "deposit", "withdraw", "retire")


def _question(args, config):
    name = config["name"]
    if args.action == "prepare":
        return f"Compile the pool contract for {name} and derive its pools' addresses? Nothing is sent."
    if args.action == "deploy":
        return f"Deploy {name}'s pools? Each costs about 13 tokens from its Safe."
    if args.action == "deposit":
        where = f"pool {args.pool}" if args.pool else "each of its pools"
        return f"Send {args.amount} tokens of the validator's own funds (plus 1 token fee) from {name}'s Safe into {where}?"
    if args.action == "withdraw":
        return f"Withdraw {args.amount} tokens of the validator's own funds from {name}'s pool {args.pool} to its Safe?"
    if args.action == "retire":
        return f"Remove {name}'s pool cycle? Nothing will count rounds, recover stakes or pay queued withdrawals."
    return f"Run denominator {args.action} for {name}?"


def handle_action(store, args):
    name = configuration.validate_node_name(args.name)
    action = args.action
    with contextlib.ExitStack() as stack:
        stack.enter_context(store.acquire_operation_lock(name))
        config = store.load_node_config(name)
        configuration.require_role(config)
        require(config)
        if action not in OFFLINE:
            scripts.require_node_console(config)
        if action not in READ_ONLY:
            ui.confirm_operation(args.yes, _question(args, config))
        if action in ELECTION_LOCKED:
            stack.enter_context(elections.hold_election_lock(store, name))
        if action == "prepare":
            config, result = prepare(store, config, source=args.contract_source,
                                     new_contract=args.new_contract, force=args.force)
        elif action == "deploy":
            config, result = deploy(store, config)
        elif action == "verify":
            config, result = verify(store, config)
        elif action == "deposit":
            config, result = deposit(store, config, amount=args.amount, label=args.pool)
        elif action == "withdraw":
            config, result = withdraw(store, config, amount=args.amount, label=args.pool)
        elif action == "info":
            result = info(store, config, label=args.pool)
        elif action == "stakers":
            result = stakers(store, config, label=args.pool)
        elif action == "link":
            result = links(config, label=args.pool, staker=args.staker)
        elif action == "retire":
            config, result = retire(store, config)
        else:
            raise process.OperationError(f"Unsupported denominator action {action}")
        store.save_node_config(config)
        ui.print_json_result(result)
    return 0


def refuse_stake_set(config):
    raise process.OperationError(
        "A denominator pool stakes everything its stakers and its validator hold, so there is "
        f"no stake to set; see the pools with evernode denominator info -n {config['name']}"
    )

