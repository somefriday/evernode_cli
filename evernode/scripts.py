"""Run explicitly supported upstream adapters in controlled node contexts."""

from . import docker, process, storage

HOST_SCRIPTS = {
    "Msig-prep.sh",
    "Msig_deploy.sh",
    "DePool-prep.sh",
    "DePool_deploy.sh",
    "stake_to_depool.sh",
    "Sign_Trans.sh",
    "prepare_elections.sh",
    "take_part_in_elections.sh",
    "part_check.sh",
}


def execute_supported_node_script(store, config, script, *, timeout=180):
    """Run an explicitly supported initialization script in a one-off container.

    Financial scripts require their own reviewed contract before being added.
    No secrets are accepted in argv/env; script failure output stays suppressed.
    """
    if script != "init_scripts/R_gen_init_configs.sh":
        raise process.OperationError("Unsupported script adapter")
    path = store.node_directory(config["name"]) / "scripts" / script
    for parent in (path.parent, path.parent.parent):
        storage.validate_private_path(parent)
    storage.validate_private_path(path)
    task_name = "evernode-init-" + config["name"]
    if docker.inspect_container(task_name):
        raise process.OperationError(
            f"Initialization container {task_name} still exists; inspect it before retrying"
        )
    try:
        return docker.execute_node_compose(
            store,
            config,
            "run",
            "--name",
            task_name,
            "--rm",
            "--no-deps",
            "--workdir",
            "/ever-node/scripts",
            "--entrypoint",
            "/bin/bash",
            "node",
            "/ever-node/scripts/" + script,
            timeout=timeout,
            sensitive=True,
        )
    except BaseException:
        # Killing the Docker client does not stop the server-side container.
        task = docker.inspect_managed_container(task_name, config["name"])
        if task:
            process.execute_command(
                ["docker", "rm", "-f", task_name], timeout=30, sensitive=True
            )
        raise


def execute_validator_script(
    store, config, script, *arguments, timeout=300, sensitive=False
):
    """Run a reviewed upstream script on the host.

    In Docker mode upstream env.sh intentionally turns its node/CLI calls into
    ``docker exec`` calls.  Running these adapters on the host keeps their
    reference paths and contract logic intact while each workspace stays
    isolated.  User input is passed as argv, never interpolated into a shell.
    """
    if script not in HOST_SCRIPTS:
        raise process.OperationError("Unsupported validator script adapter")
    root = store.node_directory(config["name"])
    path = root / "scripts" / script
    for item in (root, root / "scripts", path):
        storage.validate_private_path(item)
    return process.execute_command(
        ["bash", path, *arguments],
        cwd=root / "scripts",
        timeout=timeout,
        sensitive=sensitive,
    )


def _import_verification_context(store, config):
    root = store.node_directory(config["name"])
    scripts_dir = root / "scripts"
    for item in (
        root,
        scripts_dir,
        scripts_dir / "env.sh",
        scripts_dir / "functions.shinc",
    ):
        storage.validate_private_path(item)
    return scripts_dir


def verify_imported_safe(store, config, wallet_address, timeout=300):
    """Read an imported Safe's on-chain custodian settings only."""
    scripts_dir = _import_verification_context(store, config)
    program = 'set -euo pipefail; source ./env.sh; source ./functions.shinc; Get_Account_Custodians_Info "$EVERNODE_WALLET"'
    import os

    environment = os.environ.copy()
    environment["EVERNODE_WALLET"] = wallet_address
    result = process.execute_command(
        ["bash", "-c", program], cwd=scripts_dir, env=environment, timeout=timeout
    )
    fields = result.stdout.strip().split()
    if len(fields) < 2 or not all(item.isdigit() for item in fields[-2:]):
        raise process.OperationError(
            "Could not read imported wallet custodian settings"
        )
    return {"custodians": int(fields[-2]), "required_signatures": int(fields[-1])}


def verify_imported_depool(store, config, depool_address, timeout=300):
    """Read an imported DePool with the selected local ABI only."""
    scripts_dir = _import_verification_context(store, config)
    program = 'set -euo pipefail; source ./env.sh; source ./functions.shinc; Get_DP_Info "$EVERNODE_DEPOOL" >/dev/null'
    import os

    environment = os.environ.copy()
    environment["EVERNODE_DEPOOL"] = depool_address
    process.execute_command(
        ["bash", "-c", program], cwd=scripts_dir, env=environment, timeout=timeout
    )
    return {"address": depool_address, "checked": True}
