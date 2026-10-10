"""Public evernode command and flag definitions."""

import argparse
from pathlib import Path

from . import __version__


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
    c.add_argument("--node-repo")
    c.add_argument("--node-ref")
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
        q = node.add_parser(command)
        if command != "list":
            q.add_argument("-n", "--name")
        if command in ("stop", "remove"):
            q.add_argument("--all", action="store_true")
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
                choices=["node", "stderr", "stdout", "startup", "statsd", "elections"],
                default="node",
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
