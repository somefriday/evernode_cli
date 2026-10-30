"""Capture orchestration sources and prepare resumable node workspaces."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from . import configuration, docker, process, storage


SOURCE_COMPONENTS = ("scripts", "configs", "contracts")


def render_source_snapshot(store, config, work):
    """Render this node's recorded orchestration source into ``work``.

    The result is a complete snapshot directory holding every source component
    and the node's own rendered env.sh, plus the manifest describing it. The
    caller owns ``work`` and decides whether to install the result.
    """
    source_spec = config.get("source_spec")
    if not source_spec:
        raise process.OperationError(
            "No saved source inputs; this node's scripts cannot be rendered automatically"
        )
    if source_spec["local"]:
        source = Path(source_spec["local"])
    else:
        source = work / "checkout"
        process.execute_command(
            ["git", "clone", "--", source_spec["repo"], source], timeout=600
        )
        if source_spec["ref"]:
            process.execute_command(
                ["git", "-C", source, "checkout", "--detach", source_spec["ref"]],
                timeout=120,
            )
    revision = process.execute_command(
        ["git", "-C", source, "rev-parse", "HEAD"], check=False
    )
    staged = work / "snapshot"
    staged.mkdir(mode=0o700)
    for component in SOURCE_COMPONENTS:
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
            for c in SOURCE_COMPONENTS
        },
    }
    storage.write_json_atomically(staged / "manifest.json", manifest)
    storage.restrict_workspace_permissions(staged)
    return staged, manifest


def capture_node_source_snapshot(store, config):
    directory = store.node_directory(config["name"])
    snapshot = directory / ".prepared-source"
    if not snapshot.exists():
        if snapshot.is_symlink():
            raise process.OperationError("Refusing symlinked source snapshot")
        with tempfile.TemporaryDirectory(prefix=".prepare-", dir=directory) as tmp:
            staged, _ = render_source_snapshot(store, config, Path(tmp))
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
    for component in SOURCE_COMPONENTS:
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
        docker.ensure_container_names_available(
            store, (configuration.node_container_name(config), config["statsd"])
        )
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


def _component_files(root):
    """Map each managed file to its content hash, rejecting links."""
    storage.calculate_directory_fingerprint(root)
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _install_file(source, target):
    """Replace one file inside its existing directory.

    The workspace's scripts and contracts are bind-mounted into the running
    node container, so the directory inode must stay the same. Replacing a
    file within it is visible to the container; swapping the directory is not.
    """
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(target.name + ".evernode-sync.tmp")
    shutil.copyfile(source, temporary)
    os.chmod(
        temporary,
        0o700 if target.suffix == ".sh" and target.name != "env.sh" else 0o600,
    )
    os.replace(temporary, target)


def refresh_node_scripts(store, config, *, dry_run=False):
    """Re-render a node's saved orchestration components from its own source.

    Updating the installed package does not touch a node's saved scripts, so a
    fixed reference script otherwise never reaches an existing validator. This
    renders the recorded source again and installs only the files that differ.
    Identity, keys, database, elections state and logs are never touched.
    """
    directory = store.node_directory(config["name"])
    for component in SOURCE_COMPONENTS:
        storage.validate_private_path(directory / component)
    with tempfile.TemporaryDirectory(prefix=".refresh-", dir=directory) as tmp:
        staged, manifest = render_source_snapshot(store, config, Path(tmp))
        plan = {
            "node": config["name"],
            "project_commit": manifest["project_commit"],
            "components": {},
        }
        work = []
        for component in SOURCE_COMPONENTS:
            live = directory / component
            rendered = _component_files(staged / component)
            current = _component_files(live)
            updated = sorted(
                name
                for name, digest in rendered.items()
                if current.get(name) != digest and name in current
            )
            added = sorted(set(rendered) - set(current))
            removed = sorted(set(current) - set(rendered))
            plan["components"][component] = {
                "updated": updated,
                "added": added,
                "removed": removed,
                "unchanged": len(rendered) - len(updated) - len(added),
            }
            for name in updated + added:
                work.append((staged / component / name, live / name))
            for name in removed:
                work.append((None, live / name))
        plan["changed"] = bool(work)
        if dry_run or not work:
            return plan
        backups = directory / ".script-backups"
        backups.mkdir(exist_ok=True, mode=0o700)
        # A unique suffix keeps two refreshes in the same second apart.
        backup = Path(
            tempfile.mkdtemp(
                prefix=time.strftime("%Y%m%dT%H%M%SZ-", time.gmtime()), dir=backups
            )
        )
        backup.chmod(0o700)
        for source, target in work:
            if target.exists():
                saved = backup / target.relative_to(directory)
                saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(target, saved)
        for source, target in work:
            if source is None:
                target.unlink()
            else:
                _install_file(source, target)
        # Removing the last file in a subdirectory leaves it behind, and the
        # fingerprint below covers directories as well as files.
        for component in SOURCE_COMPONENTS:
            live = directory / component
            for path in sorted(live.rglob("*"), reverse=True):
                if (
                    path.is_dir()
                    and not any(path.iterdir())
                    and not (staged / component / path.relative_to(live)).is_dir()
                ):
                    path.rmdir()
        plan["backup"] = str(backup)
        for component in SOURCE_COMPONENTS:
            if storage.calculate_directory_fingerprint(
                directory / component
            ) != manifest["hashes"][component]:
                raise process.OperationError(
                    f"Refreshed {component} does not match the rendered source"
                )
    config["project_commit"] = manifest["project_commit"]
    config["source_hashes"] = manifest["hashes"]
    return plan
