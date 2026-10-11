"""Run explicitly supported upstream adapters in controlled node contexts."""

import json
import os

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
    """Read an imported Safe's on-chain custodian keys and threshold."""
    scripts_dir = _import_verification_context(store, config)
    program = """set -euo pipefail
source ./env.sh
source ./functions.shinc
Get_SC_current_state "$EVERNODE_WALLET" >/dev/null
boc="${INPL_ELECTIONS_WORK_DIR}/${EVERNODE_WALLET##*:}.boc"
custodians="$($CALL_CLI -j run --boc "$boc" getCustodians '{}' --abi "$INPL_SafeC_Wallet_ABI")"
parameters="$($CALL_CLI -j run --boc "$boc" getParameters '{}' --abi "$INPL_SafeC_Wallet_ABI")"
jq -cn --argjson c "$custodians" --argjson p "$parameters" \
  '{custodians: $c.custodians, required_signatures: $p.requiredTxnConfirms}'
"""

    environment = os.environ.copy()
    environment["EVERNODE_WALLET"] = wallet_address
    result = process.execute_command(
        ["bash", "-c", program], cwd=scripts_dir, env=environment, timeout=timeout
    )
    try:
        value = json.loads(result.stdout.strip().splitlines()[-1])
        keys = value["custodians"]
        if not isinstance(keys, list) or not keys:
            raise ValueError("No custodians")
        public_keys = []
        for item in keys:
            raw_key = str(item["pubkey"])
            public = int(raw_key, 16 if raw_key.lower().startswith("0x") else 10)
            if not 0 <= public < 2**256:
                raise ValueError("Invalid custodian key")
            public_keys.append(f"{public:064x}")
        signatures = int(value["required_signatures"])
        if not 1 <= signatures <= len(public_keys):
            raise ValueError("Invalid signature threshold")
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise process.OperationError(
            "Could not read imported wallet custodians and signature threshold"
        ) from exc
    return {
        "custodians": len(public_keys),
        "required_signatures": signatures,
        "public_keys": public_keys,
    }


def verify_imported_depool(store, config, depool_address, timeout=300):
    """Read an imported DePool and check which wallet it uses."""
    scripts_dir = _import_verification_context(store, config)
    program = """set -euo pipefail
source ./env.sh
source ./functions.shinc
info="$(Get_DP_Info "$EVERNODE_DEPOOL")"
jq -er '.validatorWallet' <<< "$info"
"""

    environment = os.environ.copy()
    environment["EVERNODE_DEPOOL"] = depool_address
    result = process.execute_command(
        ["bash", "-c", program], cwd=scripts_dir, env=environment, timeout=timeout
    )
    wallet_address = result.stdout.strip().splitlines()[-1]
    return {"address": depool_address, "validator_wallet": wallet_address}
