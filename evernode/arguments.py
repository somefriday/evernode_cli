"""Public evernode command and flag definitions."""

import argparse
from pathlib import Path
import re

from . import __version__

# Options whose value is a chain address, which may begin with a minus.
ADDRESS_OPTIONS = (
    "--wallet-address",
    "--depool-address",
    "--owner-address",
    "--validator-address",
    "--pay-from",
    "--address",
)
ADDRESS_VALUE = re.compile(r"^-[0-9]+:[0-9a-fA-F]{64}$")
# The vote engine's commands an operator uses (the test network's stay with
# evernode-vote itself), and evernode's own: join, leave and history, and tick,
# which only cron runs.
VOTE_COMMANDS = (
    "status",
    "param",
    "simulate",
    "propose",
    "join",
    "leave",
    "history",
    "cast",
    "run",
    "burn",
    "burn-key",
    "mint-wallet",
    "tick",
)
HIDDEN_VOTE_COMMANDS = ("tick",)
DEFAULT_STATE_DIR = "/var/lib/ever-validator"
# vote commands reach the chain here unless --endpoint names another endpoint.
DEFAULT_ENDPOINT = "https://mainnet.evercloud.dev/graphql"


def normalize_address_arguments(argv):
    """Allow a masterchain address to be written after a space.

    Every solo validator's Safe is in workchain -1, so its address starts
    with '-1:'. argparse reads any value beginning with '-' as the next
    option, which makes '--wallet-address -1:...' impossible to type and is
    exactly the form a solo import needs. Joining the pair here accepts it
    while leaving anything that is not an address to argparse's own error.
    """
    result = []
    joined = False
    for index, item in enumerate(argv):
        if joined:
            joined = False
            continue
        following = argv[index + 1] if index + 1 < len(argv) else None
        if item in ADDRESS_OPTIONS and following and ADDRESS_VALUE.match(following):
            result.append(f"{item}={following}")
            joined = True
            continue
        result.append(item)
    return result


def add_denominator_commands(groups):
    """evernode denominator: the nominator pools of a denominator validator."""
    pools = groups.add_parser(
        "denominator", help="Nominator pools that stakers deposit into, run by this validator"
    ).add_subparsers(dest="action", required=True)
    q = pools.add_parser("prepare", help="Compile the pool contract and derive the pools' addresses")
    q.add_argument("-n", "--name", required=True)
    q.add_argument(
        "--contract-source",
        help="nominator-pool contract directory; defaults to the one installed with evernode",
    )
    q.add_argument("--new-contract", action="store_true", help="move a node whose pools are deployed to new pools")
    q.add_argument("--yes", action="store_true")
    q = pools.add_parser("deploy", help="Create the pools, funded from the Safe")
    q.add_argument("-n", "--name", required=True)
    q.add_argument("--yes", action="store_true")
    q = pools.add_parser("verify", help="Check each pool's code, validator wallet and terms")
    q.add_argument("-n", "--name", required=True)
    for command, text in (
        ("deposit", "Send the validator's own funds from the Safe into the pools"),
        ("withdraw", "Take the validator's own funds out of an idle pool, to the Safe"),
    ):
        q = pools.add_parser(command, help=text)
        q.add_argument("-n", "--name", required=True)
        q.add_argument("--amount", type=int, required=True, help="Whole tokens")
        q.add_argument(
            "--pool", choices=("A", "B"), required=command == "withdraw",
            help="One pool" + ("" if command == "withdraw" else "; default: the amount goes to each"),
        )
        q.add_argument("--yes", action="store_true")
    for command, text in (
        ("info", "Where each pool's money is and what its cycle does next"),
        ("stakers", "Each pool's stakers and what they hold"),
    ):
        q = pools.add_parser(command, help=text)
        q.add_argument("-n", "--name", required=True)
        q.add_argument("--pool", choices=("A", "B"), help="One pool; default: every pool")
    q = pools.add_parser("link", help="Oberton links for stakers: stake, withdraw and the rescue steps")
    q.add_argument("-n", "--name", required=True)
    q.add_argument("--pool", choices=("A", "B"), help="One pool; default: every pool")
    q.add_argument("--staker", help="A staker's 0: address, for a link that pays its queued withdrawal")
    q = pools.add_parser("retire", help="Remove the pools' cycle once no stake is out")
    q.add_argument("-n", "--name", required=True)
    q.add_argument("--yes", action="store_true")


def build_argument_parser():
    p = argparse.ArgumentParser(
        prog="evernode",
        description="Build and manage isolated Everscale Docker validator nodes.",
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument(
        "--state-dir",
        default=DEFAULT_STATE_DIR,
        help="root-owned storage directory; put before the command",
    )
    groups = p.add_subparsers(dest="group")
    host = groups.add_parser("host").add_subparsers(dest="action", required=True)
    host.add_parser("check")
    q = host.add_parser(
        "setup",
        help="Install required Docker Engine and Compose packages on Ubuntu or Debian",
    )
    q.add_argument("--yes", action="store_true")
    q.add_argument("--dry-run", action="store_true")
    node = groups.add_parser("node").add_subparsers(dest="action", required=True)
    c = node.add_parser(
        "create", help="Create a validator node from a managed image or source build"
    )
    c.add_argument("-n", "--name")
    image_choice = c.add_mutually_exclusive_group()
    image_choice.add_argument(
        "--image",
        help="Existing managed image record/tag; skips the interactive image menu",
    )
    image_choice.add_argument(
        "--build-new",
        action="store_true",
        help="Build a new image from source; skips the interactive image menu",
    )
    c.add_argument("--ip")
    c.add_argument("--network", choices=["main", "devnet"])
    c.add_argument("--adnl-port", type=int)
    c.add_argument("--metrics-port", type=int)
    c.add_argument(
        "--memory",
        help="Explicit RAM ceiling, e.g. 40G; no automatic capacity guarantee",
    )
    c.add_argument(
        "--project-source",
        type=Path,
        help="Trusted local Ever-Validator kit instead of the one installed with evernode",
    )
    c.add_argument("--project-repo")
    c.add_argument("--project-ref")
    c.add_argument("--node-repo", help="Repository listed in the source image menu")
    c.add_argument("--node-ref", help="Source ref used with --build-new")
    c.add_argument("--cli-repo")
    c.add_argument("--cli-ref")
    c.add_argument("--image-repo")
    c.add_argument(
        "--resume",
        action="store_true",
        help="Resume saved preparation without changing inputs or identity",
    )
    c.add_argument("--yes", action="store_true")
    c.add_argument("--dry-run", action="store_true")
    s = node.add_parser(
        "setup", help="Give a created node its validator role: type, Safe and DePool"
    )
    s.add_argument("-n", "--name")
    s.add_argument(
        "--validator-type",
        choices=["depool", "solo", "nominator", "denominator"],
        help="How this validator stakes: through a DePool, solo from its own "
        "Safe, from a nominator contract a cold owner holds, or through "
        "nominator pools stakers deposit into",
    )
    s.add_argument(
        "--import-wallet",
        action="store_true",
        help="Use an existing Safe; its seed phrases are typed at wallet recover",
    )
    s.add_argument("--wallet-address")
    s.add_argument("--depool-address")
    s.add_argument("--custodians", type=int)
    s.add_argument("--required-signatures", type=int)
    s.add_argument(
        "--owner-address",
        help="Cold owner of a nominator validator's stake: a masterchain (-1:) "
        "or basechain (0:) wallet, never one this server holds",
    )
    s.add_argument("--depool-type", choices=["EverX", "StEver"])
    s.add_argument("--validator-assurance", type=int)
    s.add_argument("--min-stake", type=int)
    s.add_argument("--reward-fraction", type=int)
    s.add_argument("--balance-threshold", type=int)
    s.add_argument("--cron-interval", type=int)
    s.add_argument("--workchain", type=int)
    # A denominator validator's pools: fixed in each pool's data for life. They all
    # start with --pool, which no older flag here does, so argparse resolves every
    # abbreviation of an older flag as it did before.
    s.add_argument("--pools", type=int, choices=(1, 2), help="Nominator pools: 2 bid in alternate elections")
    s.add_argument("--pool-reward-share", dest="reward_share", type=int,
                   help="Validator's share of each pool reward, in percent")
    s.add_argument("--pool-max-stakers", dest="max_stakers", type=int, help="Most stakers a pool takes, at most 40")
    s.add_argument("--pool-min-staker-stake", dest="min_staker_stake", type=int,
                   help="Smallest staker deposit, in tokens")
    s.add_argument("--pool-min-validator-stake", dest="min_validator_stake", type=int,
                   help="Validator's own funds a pool needs to bid, in tokens")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    for command in (
        "list",
        "status",
        "config",
        "start",
        "stop",
        "restart",
        "remove",
        "logs",
        "log-level",
        "sync",
        "resources",
    ):
        if command == "resources":
            q = node.add_parser(
                command,
                help="Show a resource snapshot for one node or all managed nodes",
            )
            q.add_argument(
                "-n", "--name", help="Node name; omit to show all managed nodes"
            )
        else:
            q = node.add_parser(command)
        if command not in ("list", "resources"):
            q.add_argument("-n", "--name")
        if command in ("stop", "remove", "log-level"):
            q.add_argument("--all", action="store_true")
        if command == "log-level":
            q.add_argument(
                "level", nargs="?", choices=("info", "debug"), help="Omit to show the level"
            )
        if command in ("stop", "restart", "remove"):
            q.add_argument(
                "--force",
                action="store_true",
                help="Act even while the node is in a current or next validator set",
            )
        if command in ("stop", "restart", "remove"):
            q.add_argument("--timeout", type=int, default=30)
        if command in ("start", "stop", "restart", "remove"):
            q.add_argument("--dry-run", action="store_true")
        if command == "stop":
            at_block = q.add_mutually_exclusive_group()
            at_block.add_argument(
                "--after-block",
                type=int,
                metavar="N",
                help="Schedule the stop for when the node has applied masterchain "
                "block N and its shard blocks; cron carries it out",
            )
            at_block.add_argument(
                "--watch",
                action="store_true",
                help="Show the progress of the stop scheduled with --after-block",
            )
            at_block.add_argument(
                "--cancel",
                action="store_true",
                help="Remove the stop scheduled with --after-block",
            )
            # Cron's minute of a scheduled stop, not an operator's flag.
            q.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
        if command == "remove":
            q.add_argument("--purge-data", action="store_true")
            q.add_argument("--yes", action="store_true")
        if command == "logs":
            q.add_argument(
                "--component",
                choices=[
                    "node",
                    "stderr",
                    "stdout",
                    "startup",
                    "statsd",
                    "elections",
                    "election-cron",
                ],
                default="node",
                help="Log source; elections is the election scripts' own record of "
                "outstanding transactions, election-cron the scheduled run output and errors",
            )
            q.add_argument("--follow", action="store_true")
            q.add_argument("--tail", type=int, default=100)
        if command == "sync":
            q.add_argument("--wait", action="store_true")
            q.add_argument("--interval", type=int, default=10)
    q = node.add_parser(
        "lsync",
        help="Seed one unsynchronized local node database from another managed node",
    )
    q.add_argument(
        "--from",
        dest="source",
        required=True,
        help="Fully synchronized local source node",
    )
    q.add_argument(
        "--to", dest="target", required=True, help="Unsynchronized local target node"
    )
    q.add_argument("--yes", action="store_true")
    q.add_argument("--dry-run", action="store_true")
    q = node.add_parser(
        "sync-scripts",
        help="Refresh a node's saved orchestration scripts from its recorded source",
    )
    q.add_argument("-n", "--name", help="Node name; omit with --all")
    q.add_argument("--all", action="store_true")
    q.add_argument(
        "--bundled",
        action="store_true",
        help="Move the node to the kit installed with evernode",
    )
    q.add_argument("--yes", action="store_true")
    q.add_argument("--dry-run", action="store_true")
    wallet = groups.add_parser("wallet").add_subparsers(dest="action", required=True)
    for command in ("create", "deploy", "recover"):
        q = wallet.add_parser(command)
        q.add_argument("-n", "--name", required=True)
        q.add_argument("--yes", action="store_true")
        if command == "recover":
            q.add_argument(
                "--replace-seeds",
                action="store_true",
                help="Type the seed phrases again, replacing the stored ones",
            )
    q = wallet.add_parser("verify")
    q.add_argument("-n", "--name", required=True)
    q = wallet.add_parser("send", help="Send tokens from this node's Safe to an address")
    q.add_argument("-n", "--name", required=True)
    q.add_argument(
        "--address", required=True, help="Destination: -1: or 0: and 64 hex digits"
    )
    q.add_argument("--amount", type=int, required=True, help="Whole tokens to send")
    q.add_argument(
        "--no-bounce",
        action="store_true",
        help="Keep the tokens at the address even if it has no contract",
    )
    q.add_argument("--yes", action="store_true")
    depool = groups.add_parser("depool").add_subparsers(dest="action", required=True)
    for command in ("prepare", "deploy", "stake-initial"):
        q = depool.add_parser(command)
        q.add_argument("-n", "--name", required=True)
        q.add_argument("--yes", action="store_true")
    q = depool.add_parser("verify")
    q.add_argument("-n", "--name", required=True)
    q = depool.add_parser(
        "info", help="Read this node's DePool, rounds and own participant stake"
    )
    q.add_argument("-n", "--name", required=True)
    q = depool.add_parser(
        "withdraw",
        help="Withdraw this node's own ordinary stake, or cancel a pending withdrawal",
    )
    q.add_argument("-n", "--name", required=True)
    target = q.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--amount",
        type=int,
        help="Whole tokens to withdraw from the next round",
    )
    target.add_argument(
        "--all",
        dest="withdraw_all",
        action="store_true",
        help="Withdraw the entire ordinary stake from every round",
    )
    target.add_argument(
        "--cancel",
        action="store_true",
        help="Cancel a pending withdrawal and resume reinvestment",
    )
    q.add_argument("--yes", action="store_true")
    q = depool.add_parser(
        "confirm",
        help="Add this node's remaining signatures to one reviewed Safe transaction",
    )
    q.add_argument("-n", "--name", required=True)
    q.add_argument(
        "--transaction-id",
        required=True,
        help="Exact pending transaction id from depool info; bulk signing is refused",
    )
    q.add_argument(
        "--force",
        action="store_true",
        help="Sign even a transaction that pays none of this node's contracts",
    )
    q.add_argument("--yes", action="store_true")
    q = depool.add_parser(
        "stake", help="Add an ordinary stake to this node's DePool from its Safe"
    )
    q.add_argument("-n", "--name", required=True)
    q.add_argument("--amount", type=int, required=True, help="Whole tokens to stake")
    q.add_argument("--yes", action="store_true")
    q = depool.add_parser(
        "replenish", help="Top up the DePool's own operating balance from the Safe"
    )
    q.add_argument("-n", "--name", required=True)
    q.add_argument("--amount", type=int, required=True, help="Whole tokens to send")
    q.add_argument("--yes", action="store_true")
    stake = groups.add_parser(
        "stake", help="Per-election stake of a solo or nominator validator"
    ).add_subparsers(dest="action", required=True)
    q = stake.add_parser(
        "set", help="Record what this validator stakes in each election"
    )
    q.add_argument("-n", "--name", required=True)
    # Both forms end in a fixed number of tokens; a mode only says what
    # arithmetic to do on the spendable balance once, now.
    target = q.add_mutually_exclusive_group()
    target.add_argument("--amount", type=int, help="Whole tokens staked per election")
    target.add_argument(
        "--mode",
        choices=("balanced", "single"),
        help="Compute the stake now: balanced splits the spendable balance "
        "across two rounds, single puts all of it in one",
    )
    target.add_argument(
        "--all-balance",
        action="store_true",
        help="Deprecated alias for --mode single",
    )
    q.add_argument("--yes", action="store_true")
    q = stake.add_parser(
        "status", help="What the elector and the wallet say about this stake"
    )
    q.add_argument("-n", "--name", required=True)
    q = stake.add_parser(
        "confirm",
        help="Add this node's remaining signatures to one named Safe transaction",
    )
    q.add_argument("-n", "--name", required=True)
    q.add_argument(
        "--transaction-id",
        required=True,
        help="Exact id from stake status (nominator info for a nominator "
        "validator); bulk signing is refused",
    )
    q.add_argument(
        "--force",
        action="store_true",
        help="Sign even a transaction that pays none of this node's contracts",
    )
    q.add_argument("--yes", action="store_true")
    q = stake.add_parser(
        "recover", help="Ask the elector to return the stake of finished rounds"
    )
    q.add_argument("-n", "--name", required=True)
    q.add_argument("--yes", action="store_true")
    nominator = groups.add_parser(
        "nominator",
        help="Staking contract of a validator whose capital a cold owner holds",
    ).add_subparsers(dest="action", required=True)
    for command in ("prepare", "deploy", "verify"):
        q = nominator.add_parser(command)
        q.add_argument("-n", "--name", required=True)
        if command == "prepare":
            q.add_argument(
                "--contract-source",
                help="single-nominator contract directory; defaults to the one "
                "installed with evernode",
            )
            q.add_argument(
                "--new-contract",
                action="store_true",
                help="move a node whose contract is deployed to the new address",
            )
        q.add_argument("--yes", action="store_true")
    q = nominator.add_parser("info", help="Where this validator's money is")
    q.add_argument("-n", "--name", required=True)
    q = nominator.add_parser(
        "withdraw", help="Prepare an owner withdrawal; signing happens elsewhere"
    )
    q.add_argument("-n", "--name", required=True)
    target = q.add_mutually_exclusive_group(required=True)
    target.add_argument("--amount", type=int, help="Whole tokens to withdraw")
    target.add_argument(
        "--all", dest="withdraw_all", action="store_true",
        help="Every liquid token above the contract's reserve",
    )
    q = nominator.add_parser(
        "rotate", help="Prepare an owner replacement of the hot wallet"
    )
    q.add_argument("-n", "--name", required=True)
    q.add_argument("--validator-address", required=True)
    q = nominator.add_parser(
        "submit", help="Broadcast a message the owner signed elsewhere"
    )
    q.add_argument("-n", "--name", required=True)
    q.add_argument("--message", required=True, help="Path to the signed BOC")
    q.add_argument("--yes", action="store_true")
    q = nominator.add_parser(
        "link", help="A wallet link that sends a body to the contract; the owner confirms it"
    )
    q.add_argument("-n", "--name", required=True)
    q.add_argument("--value", type=int, help="Whole tokens to attach (default 2)")
    q.add_argument(
        "--body",
        help="Message body as a base64 BOC (default: withdraw everything above the reserve)",
    )
    add_denominator_commands(groups)
    elections = groups.add_parser("election").add_subparsers(
        dest="action", required=True
    )
    elections.add_parser("list")
    q = elections.add_parser("start")
    q.add_argument("-n", "--name", required=True)
    q.add_argument("--yes", action="store_true")
    q.add_argument("--dry-run", action="store_true")
    q = elections.add_parser("status")
    q.add_argument("-n", "--name", required=True)
    q = elections.add_parser("stop")
    q.add_argument("-n", "--name")
    q.add_argument("--all", action="store_true")
    # Stopping needs no confirmation - it only makes a node bid less - but a
    # script that passes --yes to every mutating command should not fail here
    # while the same flag works on start.
    q.add_argument(
        "--yes", action="store_true", help="Accepted for symmetry; stopping never prompts"
    )
    q.add_argument("--dry-run", action="store_true")
    image = groups.add_parser("image").add_subparsers(dest="action", required=True)
    q = image.add_parser("build")
    q.add_argument("--node-repo")
    q.add_argument("--node-ref")
    q.add_argument("--cli-repo")
    q.add_argument("--cli-ref")
    q.add_argument("--image-repo")
    q.add_argument("--rust-version")
    q.add_argument("--yes", action="store_true")
    q.add_argument("--dry-run", action="store_true")
    q = image.add_parser("rebuild")
    q.add_argument("image")
    q.add_argument("--yes", action="store_true")
    q.add_argument("--dry-run", action="store_true")
    image.add_parser("list")
    q = image.add_parser("remove")
    q.add_argument("image")
    q.add_argument("--yes", action="store_true")
    q.add_argument("--dry-run", action="store_true")
    q = node.add_parser("update")
    q.add_argument("-n", "--name", required=True)
    target = q.add_mutually_exclusive_group(required=True)
    target.add_argument("--image")
    target.add_argument("--rollback", action="store_true")
    q.add_argument("--timeout", type=int, default=30)
    q.add_argument("--yes", action="store_true")
    q.add_argument("--dry-run", action="store_true")
    q = groups.add_parser("doctor")
    q.add_argument("-n", "--name")
    q = groups.add_parser(
        "vote",
        help="Vote on a network config change: burn the config master key, set a "
        "param, upgrade code",
        description="Runs evernode-vote. Without -n or --all it needs no root and no "
        "node, only an endpoint and keys. Managed nodes vote through join, every round "
        "from cron. See evernode vote COMMAND --help.",
    )
    q.add_argument(
        "command",
        choices=VOTE_COMMANDS,
        metavar="{"
        + ",".join(c for c in VOTE_COMMANDS if c not in HIDDEN_VOTE_COMMANDS)
        + "}",
    )
    q.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        metavar="OPTIONS",
        help="evernode's vote options, then the command's own",
    )
    return p


def build_vote_schedule_parser(command):
    """Options of join, leave, history and tick, which evernode runs itself."""
    p = argparse.ArgumentParser(prog=f"evernode vote {command}", allow_abbrev=False)
    if command in ("join", "leave", "history"):
        p.add_argument(
            "--proposal",
            metavar="HASH",
            required=command != "history",
            help="The proposal's hash, as propose and status print it",
        )
    if command == "join":
        nodes = p.add_mutually_exclusive_group()
        nodes.add_argument("-n", "--name", help="Vote with this managed node's keys")
        nodes.add_argument(
            "--all",
            action="store_true",
            help="Vote with every managed node's keys (those there are now)",
        )
        p.add_argument(
            "--keyring",
            action="append",
            default=[],
            metavar="PATH",
            help="Vote with another node's config.json, or CONTAINER:/PATH inside a container",
        )
        p.add_argument(
            "--endpoint",
            metavar="URL",
            help="GraphQL endpoint; default: the one the proposal was joined with, "
            f"else {DEFAULT_ENDPOINT}",
        )
        p.add_argument("--yes", action="store_true", help="Join without asking")
    return p


def build_vote_option_parser():
    """evernode's own options of evernode vote; every other one is evernode-vote's.

    Abbreviations are off: the engine's --keys must never be read as --keyring.
    """
    p = argparse.ArgumentParser(
        prog="evernode vote COMMAND",
        add_help=False,
        allow_abbrev=False,
        description="Options evernode adds to evernode-vote's own, which follow; "
        "evernode passes --net itself.",
    )
    keys = p.add_mutually_exclusive_group()
    keys.add_argument(
        "-n", "--name", help="Vote with this managed node's validator keys (root)"
    )
    keys.add_argument(
        "--all",
        action="store_true",
        help="Vote with every managed node's validator keys (root)",
    )
    p.add_argument(
        "--keyring",
        action="append",
        default=[],
        metavar="FILE",
        help="A copy of a node's config.json with its validator_key_ring; "
        "refresh it after every election",
    )
    p.add_argument(
        "--control",
        action="append",
        default=[],
        metavar="FILE",
        help="A node's console.json: the node signs over its control server",
    )
    p.add_argument(
        "--endpoint", metavar="URL", help=f"GraphQL endpoint; default: {DEFAULT_ENDPOINT}"
    )
    payer = p.add_mutually_exclusive_group()
    payer.add_argument(
        "--pay-from-safe",
        action="store_true",
        help="propose: pay from the -n node's Safe with its key files",
    )
    payer.add_argument(
        "--pay-from",
        metavar="ADDRESS",
        help="propose: pay from this masterchain wallet; its seed phrase is asked "
        "in the terminal",
    )
    p.add_argument("--yes", action="store_true", help="Pay without asking")
    return p
