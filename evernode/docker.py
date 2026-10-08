"""Inspect owned containers, allocate ports and execute per-node Compose."""

import json
import socket

from . import configuration, process

NODE_OWNERSHIP_LABEL = "org.evernode.manager"


def select_container_names(store):
    """Return a globally unused node/exporter pair with one numeric suffix."""
    names = set(
        process.execute_command(
            ["docker", "ps", "-a", "--format", "{{.Names}}"]
        ).stdout.splitlines()
    )
    for node in store.list_node_names():
        config = store.load_node_config(node)
        names.add(configuration.node_container_name(config))
        names.add(config["statsd"])
    for index in range(1, 10000):
        suffix = f"{index:02d}"
        node, statsd = "ever-node-" + suffix, "statsd-" + suffix
        if node not in names and statsd not in names:
            return node, statsd
    raise process.OperationError("No free Docker node/container suffix is available")


def inspect_container(name):
    result = process.execute_command(
        ["docker", "container", "inspect", name], check=False
    )
    if result.returncode:
        # Distinguish a missing container from an unavailable daemon.
        process.execute_command(["docker", "info", "--format", "{{.ServerVersion}}"])
        if "No such" in result.stderr:
            return None
        raise process.OperationError(result.stderr.strip())
    return json.loads(result.stdout)[0]


def inspect_managed_container(name, node):
    obj = inspect_container(name)
    if (
        obj
        and obj.get("Config", {}).get("Labels", {}).get(NODE_OWNERSHIP_LABEL) != node
    ):
        raise process.OperationError(
            f"Container {name} is not managed by this node; refusing to change it"
        )
    return obj


def is_host_port_available(port, protocol):
    kind = socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM
    try:
        with socket.socket(socket.AF_INET, kind) as sock:
            sock.bind(("0.0.0.0", port))
    except OSError:
        return False
    # Docker bindings may use firewall forwarding without a listening socket.
    ids = process.execute_command(["docker", "ps", "-aq"]).stdout.split()
    if ids:
        for obj in json.loads(
            process.execute_command(["docker", "inspect", *ids]).stdout
        ):
            for key, bindings in (
                obj.get("HostConfig", {}).get("PortBindings") or {}
            ).items():
                if key.endswith("/" + protocol) and any(
                    int(b["HostPort"]) == port for b in bindings or []
                ):
                    return False
    return True


def select_available_node_port(store, proposed, protocol, explicit=None):
    field = "adnl_port" if protocol == "udp" else "metrics_port"
    reserved = {store.load_node_config(n)[field] for n in store.list_node_names()}
    for port in ([explicit] if explicit is not None else range(proposed, 65536)):
        if not 1024 <= port <= 65535:
            raise process.OperationError("Ports must be between 1024 and 65535")
        if port not in reserved and is_host_port_available(port, protocol):
            return port
        if explicit is not None:
            raise process.OperationError(
                f"Port {port}/{protocol} is already used or reserved"
            )
    raise process.OperationError("No free port found")


def build_node_compose_config(config, directory):
    mounts = [
        f"{directory / d}:/ever-node/{d}"
        for d in (
            "scripts",
            "configs",
            "contracts",
            "node_cfg",
            "node_db",
            "keys",
            "elections",
            "logs",
        )
    ]
    return {
        "services": {
            "statsd": {
                "image": "prom/statsd-exporter:v0.26.0",
                "container_name": config["statsd"],
                "labels": {NODE_OWNERSHIP_LABEL: config["name"]},
                "restart": "unless-stopped",
                # Default mapping already turns rnode.foo into rnode_foo; no secret or config mounts.
                "ports": [f"127.0.0.1:{config['metrics_port']}:9102"],
                "networks": ["node"],
                "logging": {
                    "driver": "json-file",
                    "options": {"max-size": "20m", "max-file": "3"},
                },
            },
            "node": {
                "image": config["image_id"],
                "container_name": configuration.node_container_name(config),
                "hostname": config["name"],
                "labels": {NODE_OWNERSHIP_LABEL: config["name"]},
                "restart": "unless-stopped",
                "init": True,
                "mem_limit": config["memory"],
                "stop_grace_period": "30s",
                "environment": {
                    "STATSD_DOMAIN": config["statsd"],
                    "STATSD_PORT": "9125",
                },
                "ports": [f"{config['adnl_port']}:{config['adnl_port']}/udp"],
                "volumes": mounts,
                # Run only the node: legacy entrypoint enables cron and modifies permissions.
                "entrypoint": ["/bin/bash", "-ec"],
                "command": [
                    "source /ever-node/scripts/env.sh; exec /usr/local/bin/ever-node --configs /ever-node/node_cfg >> /ever-node/logs/node/stdout.log 2>> /ever-node/logs/node/stderr.log"
                ],
                "networks": ["node"],
            },
        },
        "networks": {"node": {}},
    }


def execute_node_compose(store, config, *args, timeout=120, sensitive=False):
    return process.execute_command(
        [
            "docker",
            "compose",
            "--project-name",
            configuration.node_compose_project(config),
            "--file",
            store.node_directory(config["name"]) / "compose.json",
            *args,
        ],
        timeout=timeout,
        sensitive=sensitive,
    )
