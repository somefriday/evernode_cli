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
)
ADDRESS_VALUE = re.compile(r"^-[0-9]+:[0-9a-fA-F]{64}$")


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


def build_argument_parser():
    p = argparse.ArgumentParser(
        prog="evernode",
        description="Build and manage isolated Everscale Docker validator nodes.",
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument(
        "--state-dir",
        default="/var/lib/ever-validator",
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
    c.add_argument(
        "--validator-type",
        choices=["depool", "solo", "nominator"],
        help="How this validator stakes: through a DePool, or solo from its own Safe",
    )
    c.add_argument("--network", choices=["main", "devnet"])
    c.add_argument("--workchain", type=int)
    c.add_argument("--adnl-port", type=int)
    c.add_argument("--metrics-port", type=int)
    c.add_argument(
        "--memory",
        help="Explicit RAM ceiling, e.g. 40G; no automatic capacity guarantee",
    )
    c.add_argument(
        "--project-source", type=Path, help="Trusted local Ever-Validator checkout"
    )
    c.add_argument("--project-repo")
    c.add_argument("--project-ref")
    c.add_argument("--node-repo", help="Repository listed in the source image menu")
    c.add_argument("--node-ref", help="Source ref used with --build-new")
    c.add_argument("--cli-repo")
    c.add_argument("--cli-ref")
    c.add_argument("--image-repo")
    c.add_argument("--cron-interval", type=int)
    c.add_argument("--depool-type", choices=["EverX", "StEver"])
    c.add_argument("--validator-assurance", type=int)
    c.add_argument("--min-stake", type=int)
    c.add_argument("--reward-fraction", type=int)
    c.add_argument("--balance-threshold", type=int)
    c.add_argument(
        "--import-wallet",
        action="store_true",
        help="Recover an existing Safe multisig from seed phrases",
    )
    c.add_argument("--custodians", type=int)
    c.add_argument("--required-signatures", type=int)
    c.add_argument(
        "--owner-address",
        help="Cold owner of a nominator validator's stake; masterchain, and "
        "never a wallet this server holds",
    )
    c.add_argument("--wallet-address")
    c.add_argument("--depool-address")
    c.add_argument(
        "--resume",
        action="store_true",
        help="Resume saved preparation without changing inputs or identity",
    )
    c.add_argument("--yes", action="store_true")
    c.add_argument("--dry-run", action="store_true")
    for command in (
        "list",
        "status",
        "config",
        "start",
        "stop",
        "restart",
        "remove",
        "logs",
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
        if command in ("stop", "remove"):
            q.add_argument("--all", action="store_true")
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
    q.add_argument("--yes", action="store_true")
    q.add_argument("--dry-run", action="store_true")
    wallet = groups.add_parser("wallet").add_subparsers(dest="action", required=True)
    for command in ("create", "deploy", "recover"):
        q = wallet.add_parser(command)
        q.add_argument("-n", "--name", required=True)
        q.add_argument("--yes", action="store_true")
    q = wallet.add_parser("verify")
    q.add_argument("-n", "--name", required=True)
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
        "stake", help="Per-election stake of a solo validator"
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
        help="Exact id from stake status; bulk signing is refused",
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
                help="Vendored single-nominator directory; defaults to the "
                "sibling of this node's recorded project source",
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
    return p
