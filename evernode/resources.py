"""Read-only resource snapshots for managed validator containers."""

import shutil
import sys

from . import configuration, disk_usage, docker, process


def print_container_resources(config):
    """Print both running containers in one Docker table with a shared header."""
    node = config["name"]
    running = []
    failed = False
    for container in (configuration.node_container_name(config), config["statsd"]):
        try:
            obj = docker.inspect_managed_container(container, node)
            if obj is None:
                print(f"{container}: absent")
                continue
            status = obj["State"]["Status"]
            if obj["State"].get("OOMKilled") is True:
                print(f"WARNING: {container} was OOM-killed.", flush=True)
            if status == "running":
                running.append(container)
            else:
                print(f"{container}: {status}")
        except (process.OperationError, OSError, ValueError) as exc:
            print(f"Error: {node}/{container}: {exc}", file=sys.stderr)
            failed = True
    if running:
        try:
            print(
                process.execute_command(
                    ["docker", "stats", "--no-stream", *running]
                ).stdout,
                end="",
            )
        except (process.OperationError, OSError, ValueError) as exc:
            print(
                f"Error: {node}: container statistics unavailable: {exc}",
                file=sys.stderr,
            )
            failed = True
    return failed


def print_workspace_resources(directory, node, seen_filesystems):
    print(f"\nNode data on disk — {node} (scanning...)", flush=True)
    sizes = disk_usage.measure_workspace(directory)
    total = sizes[directory]
    database = sizes.get(directory / "node_db", 0)
    logs = sizes.get(directory / "logs", 0)
    other = total - database - logs
    print(f"{'DATABASE':<14}{'LOGS':<14}{'OTHER':<14}{'TOTAL':<14}")
    print(
        "".join(
            f"{disk_usage.format_bytes(value):<14}"
            for value in (database, logs, other, total)
        )
    )

    # Include top-level directories so a separately mounted database is reported.
    for path in sorted(sizes):
        device = path.stat().st_dev
        if device in seen_filesystems:
            continue
        usage = shutil.disk_usage(path)
        print(
            f"Filesystem containing {path}: "
            f"{disk_usage.format_bytes(usage.total)} capacity, "
            f"{disk_usage.format_bytes(usage.free)} available"
        )
        seen_filesystems.add(device)


def show_node_resources(store, name=None):
    """Report one node or all registered nodes, continuing after local failures."""
    names = (
        [configuration.validate_node_name(name)]
        if name is not None
        else store.list_node_names()
    )
    if not names:
        print("No managed nodes found.")
        return 0

    failed = False
    seen_filesystems = set()
    for node in names:
        if name is None:
            print(f"\n=== {node} ===", flush=True)
        try:
            config = store.load_node_config(node)
        except (process.OperationError, OSError, ValueError) as exc:
            print(f"Error: {node}: {exc}", file=sys.stderr)
            failed = True
            continue
        if print_container_resources(config):
            failed = True
        try:
            print_workspace_resources(
                store.node_directory(node), node, seen_filesystems
            )
        except (process.OperationError, OSError, ValueError) as exc:
            print(
                f"Error: {node}: disk report incomplete or unavailable: {exc}",
                file=sys.stderr,
            )
            failed = True
    return 1 if failed else 0
