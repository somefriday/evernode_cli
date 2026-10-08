"""Capture orchestration sources and prepare resumable node workspaces."""

import json
import os
from pathlib import Path
import shutil
import tempfile

from . import configuration, docker, process, storage


def capture_node_source_snapshot(store, config):
    directory = store.node_directory(config["name"])
    snapshot = directory / ".prepared-source"
    if not snapshot.exists():
        if snapshot.is_symlink():
            raise process.OperationError("Refusing symlinked source snapshot")
        source_spec = config.get("source_spec")
        if not source_spec:
            raise process.OperationError(
                "No saved source inputs; this preparation cannot be resumed automatically"
            )
        with tempfile.TemporaryDirectory(prefix=".prepare-", dir=directory) as tmp:
            work = Path(tmp)
            if source_spec["local"]:
                source = Path(source_spec["local"])
            else:
                source = work / "checkout"
                process.execute_command(
                    ["git", "clone", "--", source_spec["repo"], source], timeout=600
                )
                if source_spec["ref"]:
                    process.execute_command(
                        [
                            "git",
                            "-C",
                            source,
                            "checkout",
                            "--detach",
                            source_spec["ref"],
                        ],
                        timeout=120,
                    )
            revision = process.execute_command(
                ["git", "-C", source, "rev-parse", "HEAD"], check=False
            )
            staged = work / "snapshot"
            staged.mkdir(mode=0o700)
            for component in ("scripts", "configs", "contracts"):
                storage.calculate_directory_fingerprint(source / component)
                shutil.copytree(source / component, staged / component)
            configuration.write_node_environment(
                staged / "scripts/env.sh", configuration.build_node_environment(config)
            )
            manifest = {
                "project_commit": (
                    revision.stdout.strip()
                    if revision.returncode == 0
                    else "local-unversioned"
                ),
                "inputs": configuration.build_node_environment(config),
                "hashes": {
                    c: storage.calculate_directory_fingerprint(staged / c)
                    for c in ("scripts", "configs", "contracts")
                },
            }
            storage.write_json_atomically(staged / "manifest.json", manifest)
            storage.restrict_workspace_permissions(staged)
            os.replace(staged, snapshot)
    storage.validate_private_path(snapshot)
    storage.validate_private_path(snapshot / "manifest.json")
    manifest = json.loads((snapshot / "manifest.json").read_text())
    if (
        not isinstance(manifest, dict)
        or not isinstance(manifest.get("hashes"), dict)
        or not isinstance(manifest.get("project_commit"), str)
    ):
        raise process.OperationError("Invalid saved source snapshot manifest")
    if manifest.get("inputs") != configuration.build_node_environment(config):
        raise process.OperationError(
            "Saved source snapshot does not match node configuration"
        )
    for component in ("scripts", "configs", "contracts"):
        if storage.calculate_directory_fingerprint(
            snapshot / component
        ) != manifest.get("hashes", {}).get(component):
            raise process.OperationError(
                "Saved source snapshot was modified; refusing automatic resume"
            )
    return snapshot, manifest


def prepare_node_workspace(store, config):
    """Resume preparation from an immutable rendered source snapshot, never keys."""
    if config["phase"] != "preparing":
        return  # A retry after completed preparation is a no-op.
    if config["initialized"]:
        raise process.OperationError(
            "Invalid preparation state: identity is already initialized"
        )
    directory = store.node_directory(config["name"])
    for component in ("node_cfg", "keys"):
        path = directory / component
        if path.is_symlink() or (path.exists() and any(path.iterdir())):
            raise process.OperationError(
                "Identity files exist in an unfinished preparation; refusing automatic changes"
            )
    # Verify the pinned image still exists; never resolve a mutable tag on resume.
    process.execute_command(["docker", "image", "inspect", config["image_id"]])
    snapshot, manifest = capture_node_source_snapshot(store, config)
    config["project_commit"] = manifest["project_commit"]
    config["source_hashes"] = manifest["hashes"]
    config["prepare_stage"] = "source-captured"
    store.save_node_config(config)
    for component in ("scripts", "configs", "contracts"):
        target = directory / component
        if target.exists() or target.is_symlink():
            if (
                storage.calculate_directory_fingerprint(target)
                != manifest["hashes"][component]
            ):
                raise process.OperationError(
                    f"Partial workspace {component} differs from the saved snapshot; refusing overwrite"
                )
        else:
            with tempfile.TemporaryDirectory(prefix=".copy-", dir=directory) as tmp:
                staged = Path(tmp) / component
                shutil.copytree(snapshot / component, staged)
                storage.restrict_workspace_permissions(staged)
                os.replace(staged, target)
    for folder in (
        "node_cfg",
        "keys",
        "node_db",
        "elections",
        "elections/elections_hist",
        "logs",
        "logs/node",
        "logs/validator",
        "logs/archives",
    ):
        path = directory / folder
        path.mkdir(exist_ok=True, mode=0o700)
        storage.validate_private_path(path)
    storage.write_json_atomically(
        directory / "compose.json", docker.build_node_compose_config(config, directory)
    )
    config.update(phase="prepared", prepare_stage="complete")
    store.save_node_config(config)


def provision_node_workspace(
    store,
    config,
    project_source=None,
    project_repo=configuration.DEFAULT_ORCHESTRATION_REPOSITORY,
    project_ref=None,
):
    """Reserve first under the registry lock, then prepare under the caller's node lock."""
    directory = store.node_directory(config["name"])
    with store.acquire_operation_lock():
        if directory.exists() or directory.is_symlink():
            raise process.OperationError(
                "Node directory already exists; use node create --resume -n NAME for an interrupted preparation"
            )
        for name in (configuration.node_container_name(config), config["statsd"]):
            if docker.inspect_container(name):
                raise process.OperationError(f"Container name already exists: {name}")
        process.execute_command(["docker", "image", "inspect", config["image_id"]])
        for field, protocol in (("adnl_port", "udp"), ("metrics_port", "tcp")):
            docker.select_available_node_port(
                store, config[field], protocol, config[field]
            )
        config.update(
            schema_version=1,
            phase="preparing",
            initialized=False,
            prepare_stage="reserved",
            source_spec={
                "local": (
                    str(Path(project_source).resolve()) if project_source else None
                ),
                "repo": project_repo,
                "ref": project_ref,
            },
            project_source=(
                str(Path(project_source).resolve()) if project_source else project_repo
            ),
        )
        configuration.validate_saved_node_config(config, config["name"])
        directory.parent.mkdir(exist_ok=True, mode=0o700)
        storage.validate_private_path(directory.parent)
        directory.mkdir(mode=0o700)
        store.save_node_config(config)
    prepare_node_workspace(store, config)


def validate_generated_node_configuration(directory, config):
    """Check generated paths/network before allowing the node to start."""
    cfg_dir = directory / "node_cfg"
    node = json.loads((cfg_dir / "config.json").read_text())
    defaults = json.loads((cfg_dir / "default_config.json").read_text())
    console = json.loads((cfg_dir / "console.json").read_text())
    if not all(isinstance(v, dict) for v in (node, defaults, console)):
        raise process.OperationError(
            "Generated configuration must contain JSON objects"
        )
    if (
        defaults.get("ip_address") != f"{config['ip']}:{config['adnl_port']}"
        or defaults.get("control_server_port") != 5888
    ):
        raise process.OperationError(
            "Generated configuration does not match the requested ports/address"
        )
    if (
        node.get("internal_db_path") != "/ever-node/node_db"
        or not node.get("adnl_node")
        or not node.get("control_server")
    ):
        raise process.OperationError(
            "Generated node configuration is incomplete or has an unexpected database path"
        )
    client = console.get("config")
    if (
        not isinstance(client, dict)
        or client.get("server_address") != "127.0.0.1:5888"
        or not client.get("client_key")
        or not client.get("server_key")
    ):
        raise process.OperationError(
            "Generated console configuration is incomplete or has an unexpected address"
        )
    global_path = Path(node.get("ton_global_config_name", ""))
    if global_path.parent != Path("/ever-node/node_cfg") or not global_path.name:
        raise process.OperationError(
            "Generated global config path is outside the node configuration directory"
        )
    network_dir = "mainnet" if config["network"] == "main" else "devnet"
    actual = json.loads((cfg_dir / global_path.name).read_text())
    expected = json.loads(
        (directory / "configs" / network_dir / global_path.name).read_text()
    )
    if (
        not isinstance(actual, dict)
        or not actual.get("validator", {}).get("zero_state")
        or actual != expected
    ):
        raise process.OperationError(
            "Generated global configuration does not match the selected source network"
        )
