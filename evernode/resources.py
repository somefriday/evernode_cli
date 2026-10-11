"""Read-only resource snapshots for managed validator containers."""

import sys

from . import configuration, docker, process, ui


def print_container_resources(container, node):
    obj = docker.inspect_managed_container(container, node)
    if obj is None:
        ui.print_json_result({"container": container, "status": "absent"})
        return

    status = obj["State"]["Status"]
    if status == "running":
        print(
            process.execute_command(
                ["docker", "stats", "--no-stream", container]
            ).stdout,
            end="",
        )
    details = {
        "container": container,
        "memory_limit_bytes": obj["HostConfig"]["Memory"],
        "oom_killed": obj["State"]["OOMKilled"],
    }
    if status != "running":
        details["status"] = status
    ui.print_json_result(details)


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
    for node in names:
        if name is None:
            print(f"\n=== {node} ===", flush=True)
        try:
            config = store.load_node_config(node)
        except (process.OperationError, OSError, ValueError) as exc:
            print(f"Error: {node}: {exc}", file=sys.stderr)
            failed = True
            continue
        for container in (configuration.node_container_name(config), config["statsd"]):
            try:
                print_container_resources(container, node)
            except (process.OperationError, OSError, ValueError) as exc:
                print(f"Error: {node}/{container}: {exc}", file=sys.stderr)
                failed = True
    return 1 if failed else 0
