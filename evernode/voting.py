"""Validator votes on the network configuration: evernode vote COMMAND.

The votes are evernode-vote's, a program built from this repository's vote/
directory. It reaches the chain through a GraphQL endpoint, checks every
proposal and vote against the config contract's rules, dry-runs each message
on the contracts' own code, and follows every vote until the config counts it.
This module only tells it what evernode knows: where a managed node's
validator keys are, and which wallet pays. That description goes through a
pipe, so a seed phrase or an endpoint's project key never shows in the
engine's arguments or environment (EVERNODE_ENDPOINT is not passed on), and
the engine's errors never name the endpoint. No file holds a seed phrase; a
joined proposal's endpoint is kept in the root-only vote store while it is
open.

Managed nodes vote only through join (vote_schedule.py), which votes in every
round from cron and records each vote. Every payment goes through propose,
which shows the price and asks first.

Without -n or --all this is evernode-vote itself: no root, no node and no
Docker, only an endpoint and the keys named on the command line.
"""

import getpass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from . import arguments, configuration, elections, process, storage, wallets

ENGINE = "evernode-vote"
# The engine release this module speaks to; vote/Cargo.toml has the same version.
ENGINE_VERSION = "0.2.5"
# host setup builds it from the vote/ sources this package carries.
INSTALL = "sudo evernode host setup"
# Commands that read neither the chain nor a key, and those that sign votes.
OFFLINE_COMMANDS = ("burn-key", "mint-wallet")
VOTING_COMMANDS = ("cast", "run", "burn")
# Engine options this group sets itself, or keeps off the command line.
REFUSED_OPTIONS = {
    "--net": "evernode describes the network itself; run evernode-vote for a net file",
    "--secret": "secrets are never accepted as command arguments",
    "--lock": "evernode names the lock itself: the node's election lock, for --pay-from-safe",
}


def handle_vote(args):
    """Run one engine command for evernode vote; return its exit code."""
    for option, reason in REFUSED_OPTIONS.items():
        if any(item == option or item.startswith(option + "=") for item in args.args):
            raise process.OperationError(f"{option}: {reason}")
    if args.command in OFFLINE_COMMANDS:
        # They read no chain and no key, so evernode adds no options to them, and
        # mint-wallet's own --name must not be read as evernode's -n.
        return run_engine(find_engine(), args.command, args.args)
    # vote_schedule imports this module, so the import stays inside the call.
    from . import vote_schedule

    if args.command in vote_schedule.COMMANDS:
        options = arguments.build_vote_schedule_parser(args.command).parse_args(args.args)
        return vote_schedule.handle(args, options)
    parser = arguments.build_vote_option_parser()
    options, rest = parser.parse_known_args(args.args)
    if "-h" in rest or "--help" in rest:
        parser.print_help()
        print()
        return run_engine(find_engine(), args.command, ["--help"])
    if args.command in VOTING_COMMANDS and (options.name or options.all):
        raise process.OperationError(managed_vote_refusal(args.command, options, rest))
    from_file = any(item == "--from" or item.startswith("--from=") for item in rest)
    if (options.pay_from_safe or options.pay_from) and from_file:
        raise process.OperationError(
            "Pay from one wallet: --pay-from-safe, --pay-from or --from"
        )
    paying = options.pay_from_safe or options.pay_from or from_file
    if paying and args.command != "propose":
        raise process.OperationError(
            "--pay-from-safe, --pay-from and --from pay only with propose, which shows the "
            "price and asks first. Then vote for the hash it prints: sudo evernode vote join "
            "on a server with managed nodes, or evernode vote cast --proposal HASH --watch "
            "with key files"
        )
    if options.pay_from_safe and not options.name:
        raise process.OperationError(
            "--pay-from-safe pays from one node's Safe: name the node with -n"
        )
    # A relay is a wallet file too: its key spends what carries every vote.
    if any(item == "--relay" or item.startswith("--relay=") for item in rest):
        require_private_wallet(Path(wallet_file(rest, "--relay")))
    endpoint =options.endpoint or os.environ.get("EVERNODE_ENDPOINT")
    if not endpoint:
        raise process.OperationError(
            "Name the GraphQL endpoint: set EVERNODE_ENDPOINT or pass --endpoint URL"
        )
    net = {"endpoint": endpoint, "nodes": []}
    store = None
    if options.name or options.all:
        if os.geteuid() != 0:
            raise process.OperationError(
                "Use sudo evernode vote with -n or --all: managed node keys are root's"
            )
        store = storage.NodeStateStore(args.state_dir)
        names = [options.name] if options.name else store.list_node_names()
        net["nodes"] += managed_key_rings(store, names, skip_missing=options.all)
    for kind in ("keyring", "control"):
        # A key ring holds the validator's secret keys, a console file the console's.
        for path in getattr(options, kind):
            require_private_file(Path(path))
        net["nodes"] += [
            {"name": path, kind: str(Path(path).absolute())}
            for path in getattr(options, kind)
        ]
    if args.command in VOTING_COMMANDS and not net["nodes"]:
        raise process.OperationError(
            f"{args.command} signs with validator keys named by file: --keyring FILE or "
            "--control FILE. Managed nodes vote through join"
        )
    engine = find_engine()
    if paying:
        return pay_and_propose(engine, rest, net, store, options)
    code = run_engine(engine, args.command, rest, net)
    if args.command == "status" and code == 0 and os.geteuid() == 0:
        vote_schedule.print_status(args.state_dir)
    return code


def managed_vote_refusal(command, options, rest):
    """What to run instead of cast, run or burn with a managed node's keys."""
    nodes = f"-n {options.name}" if options.name else "--all"
    if command == "cast":
        hash_ = next((rest[i + 1] for i, item in enumerate(rest[:-1]) if item == "--proposal"), "HASH")
        return (
            "Managed nodes vote through join, which votes now and in every round "
            f"and records each vote: sudo evernode vote join {nodes} --proposal {hash_}"
        )
    return (
        f"Managed nodes vote through join: propose the change first (sudo evernode vote "
        f"propose ... --pay-from-safe or --from FILE), then sudo evernode vote join {nodes} "
        "--proposal HASH with the hash it prints"
    )


def managed_key_rings(store, names, *, skip_missing):
    """Managed nodes' key rings, read in place.

    ever-node rewrites node_cfg/config.json in place at every election and
    keeps the mode evernode gave it, so the file on the host always holds the
    current validator keys. --all passes over a removed node and a node that
    has no key ring yet, but not over every node: a join with no key ring to
    vote with is refused.
    """
    if not names:
        raise process.OperationError("No managed nodes. Use evernode node create")
    nodes = []
    for name in names:
        config = store.load_node_config(name)
        # node remove keeps the node's directory, key ring included, but the node no longer validates.
        if skip_missing and config.get("phase") == "removed":
            print(f"{name}: removed; skipped", file=sys.stderr)
            continue
        keyring = store.node_directory(name) / "node_cfg" / "config.json"
        if not keyring.exists():
            if skip_missing:
                print(f"{name}: no key ring yet; skipped", file=sys.stderr)
                continue
            raise process.OperationError(f"{name}: no key ring yet; start its node first")
        storage.validate_private_path(keyring.parent)
        storage.validate_private_path(keyring)
        nodes.append({"name": name, "keyring": str(keyring)})
    if not nodes:
        raise process.OperationError(
            "No managed node has a key ring yet, so none could vote: start the nodes, then join"
        )
    return nodes


# join --keyring: a node's config.json on this host, or CONTAINER:/PATH in a running container.
CONTAINER_KEY_RING = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*):(/.+)")


def key_ring_source(value):
    """A --keyring as join stores it: a container source as given, a file as an absolute path."""
    return value if CONTAINER_KEY_RING.fullmatch(value) else str(Path(value).absolute())


def read_key_ring(source, directory):
    """The engine's node for the key ring of a node evernode does not manage, read now.

    ever-node rewrites its config.json at every election, so every pass reads
    it again. A file on this host is used in place. One in a container is
    copied with docker exec into a private file in `directory`, which the
    caller removes after the pass.
    """
    container = CONTAINER_KEY_RING.fullmatch(source)
    if container:
        try:
            # Its output is the secret key ring: an error shows only docker's own message.
            result = process.execute_command(
                ["docker", "exec", container[1], "cat", container[2]], check=False, timeout=30
            )
        except process.OperationError as exc:
            raise process.OperationError(f"{source}: {exc}") from None
        if result.returncode:
            raise process.OperationError(f"{source}: {result.stderr.strip() or 'docker exec failed'}")
        text = result.stdout
    else:
        try:
            text = Path(source).read_text()
        except OSError as exc:
            raise process.OperationError(f"{source}: {exc.strerror}") from None
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if not isinstance(value, dict) or "validator_key_ring" not in value:
        raise process.OperationError(
            f"{source} has no validator_key_ring: name the node's config.json"
        )
    if not container:
        return {"name": source, "keyring": source}
    descriptor, path = tempfile.mkstemp(dir=directory, suffix=".json")
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)
    return {"name": source, "keyring": path}


def pay_and_propose(engine, rest, net, store, options):
    """propose, paid from the wallet evernode was told to use.

    A node's Safe pays under the node's operation and election locks, like
    every other spend from it. The engine takes the election lock itself,
    after the operator answered and before it pays, so a question left open
    holds up no election run. A campaign runs for days and must not hold
    them, so join only votes.
    """
    if "--dry-run" in rest:
        return run_engine(engine, "propose", rest, net)
    if not (options.pay_from or options.pay_from_safe):
        # --from FILE: the engine reads the wallet file itself
        require_private_wallet(Path(wallet_file(rest)))
        return paid_propose(engine, rest, net, options.yes, None)
    if options.pay_from:
        if not sys.stdin.isatty():
            raise process.OperationError(
                "--pay-from asks for the wallet's seed phrase in a terminal; seed "
                "phrases are never accepted as command arguments"
            )
        payer = {"name": options.pay_from, "address": options.pay_from}
        return paid_propose(engine, rest, net, options.yes, payer, ask_phrase=True)
    # A DePool validator's Safe can never pay: say so before waiting for any lock.
    require_masterchain_safe(store.load_node_config(options.name))
    with store.acquire_operation_lock(options.name):
        safe = node_safe(store, store.load_node_config(options.name))
        lock = elections.election_lock_path(store, options.name)
        storage.validate_private_path(lock.parent)
        return paid_propose(engine, [*rest, "--lock", str(lock)], net, options.yes, safe)


def paid_propose(engine, rest, net, yes, payer, *, ask_phrase=False):
    """Run the engine's propose: it shows the change and its price, asks, and
    pays exactly what it showed, in one process (--yes: without asking).

    Without `payer` the wallet is the --from file in `rest`.
    """
    if ask_phrase:
        phrase = getpass.getpass(f"Seed phrase of {payer['address']}: ")
        payer = dict(payer, phrases=[phrase])
    if yes:
        rest = [*rest, "--yes"]
    return run_engine(engine, "propose", rest, net if payer is None else dict(net, payer=payer))


def wallet_file(rest, option="--from"):
    for i, item in enumerate(rest):
        if item.startswith(option + "="):
            return item.split("=", 1)[1]
        if item == option and i + 1 < len(rest):
            return rest[i + 1]
    return "?"


def require_private_key_ring(source):
    """Refuse a join --keyring source that other users can read, on this host or in its container.

    The mode is only read, never changed: the operator decides. A pass reads
    the key ring without checking again, so a mode changed during a campaign
    does not stop its votes.
    """
    container = CONTAINER_KEY_RING.fullmatch(source)
    if not container:
        require_private_file(Path(source))
        return
    try:
        result = process.execute_command(
            ["docker", "exec", container[1], "stat", "-L", "-c", "%a", container[2]],
            check=False,
            timeout=30,
        )
    except process.OperationError as exc:
        raise process.OperationError(f"{source}: {exc}") from None
    if result.returncode:
        raise process.OperationError(f"{source}: {result.stderr.strip() or 'docker exec failed'}")
    try:
        mode = int(result.stdout.strip(), 8)
    except ValueError:
        raise process.OperationError(
            f"{source}: cannot tell who can read it (stat printed {result.stdout.strip()[:20]!r})"
        ) from None
    if mode & 0o077:
        raise process.OperationError(
            f"{source} holds a secret key and other users can read it: "
            f"docker exec {container[1]} chmod 600 {container[2]}"
        )


def require_private_file(path):
    """Refuse a file that holds a secret key when other users can read it.

    Only the mode is checked, not the owner: the file is made by the operator and read under sudo.
    """
    try:
        mode = path.stat().st_mode
    except OSError:
        return  # the engine says what is wrong with a file it cannot read
    if mode & 0o077:
        raise process.OperationError(
            f"{path} holds a secret key and other users can read it: chmod 600 {path}"
        )


def require_private_wallet(path):
    """A wallet file and the key files it names hold secrets: refuse one that other users can read."""
    try:
        names = json.loads(path.read_text()).get("keys") or []
        names = [name for name in names if isinstance(name, str)]
    except (OSError, ValueError, AttributeError, TypeError):
        return  # the engine says what is wrong with a file it cannot read
    for item in [path, *(path.parent / name for name in names)]:
        require_private_file(item)


def require_masterchain_safe(config):
    """The config contract takes payment only from the masterchain."""
    configuration.require_role(config)
    # The address decides once the Safe exists: an imported DePool validator's
    # Safe may be in the masterchain whatever workchain its node works in.
    address = config["wallet"].get("wallet_address")
    workchain = int(address.split(":")[0]) if address else wallets.wallet_workchain(config)
    if workchain != -1:
        raise process.OperationError(
            f"{config['name']}'s Safe is in workchain {workchain}, and the config contract takes "
            "payment only from the masterchain: pay with --pay-from ADDRESS or "
            "--from FILE (evernode vote mint-wallet makes one)"
        )


def node_safe(store, config):
    """The node's Safe for the engine: its address and every custodian's key file."""
    name = config["name"]
    require_masterchain_safe(config)
    keys = store.node_directory(name) / "keys"
    address_file = keys / f"{name}.addr"
    storage.validate_private_path(address_file)
    address = address_file.read_text().strip()
    if not wallets.ADDRESS_PATTERN.fullmatch(address):
        raise process.OperationError(f"{address_file} holds no wallet address")
    custodians = wallets.node_wallet_profile(config)["custodians"]
    key_files = [keys / f"{name}_{i}.keys.json" for i in range(1, custodians + 1)]
    for path in key_files:
        storage.validate_private_path(path)
    return {
        "name": f"{name}'s Safe",
        "type": "multisig",
        "address": address,
        "keys": [str(path) for path in key_files],
    }


def find_engine():
    """The installed engine, if it is the release this evernode speaks to."""
    path = shutil.which(ENGINE)
    if path is None:
        raise process.OperationError(f"{ENGINE} is not installed: run {INSTALL}")
    found = process.execute_command([path, "--version"], timeout=30).stdout.split()
    if found != [ENGINE, ENGINE_VERSION]:
        raise process.OperationError(
            f"{path} is {' '.join(found) or 'unknown'}; this evernode needs "
            f"{ENGINE} {ENGINE_VERSION}: run {INSTALL}"
        )
    return path


def engine_environment():
    """evernode's environment without EVERNODE_ENDPOINT: the endpoint, which may
    hold a project key, reaches the engine only through the description pipe."""
    return {k: v for k, v in os.environ.items() if k != "EVERNODE_ENDPOINT"}


def run_engine(engine, command, rest, net=None):
    """Run one engine command in the operator's terminal; return its exit code.

    The network description goes through a pipe that the engine reads as its
    net file, /dev/fd/N.
    """
    # What evernode printed comes first; the engine writes to the same terminal.
    sys.stdout.flush()
    if net is None:
        child = subprocess.Popen([engine, command, *rest], env=engine_environment())
    else:
        read, write = os.pipe()
        try:
            child = subprocess.Popen(
                [engine, command, "--net", f"/dev/fd/{read}", *rest],
                pass_fds=(read,),
                env=engine_environment(),
            )
        except BaseException:
            os.close(write)
            raise
        finally:
            os.close(read)
        try:
            with os.fdopen(write, "w") as pipe:
                json.dump(net, pipe)
        except BrokenPipeError:
            pass  # the engine stopped before reading, and said why
    try:
        return child.wait()
    except KeyboardInterrupt:
        # The interrupt reached the engine as well. What it sent is on chain or
        # not, as it printed; nothing else is left to undo.
        child.wait()
        return 130
