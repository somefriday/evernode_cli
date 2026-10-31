"""Run explicitly supported upstream adapters in controlled node contexts."""

import json
import os
import re
import time

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

# These two only generate keys, addresses and config files. Every other host
# script reads the chain or sends a message, so it needs the node's console.
OFFLINE_SCRIPTS = {
    "Msig-prep.sh",
    "DePool-prep.sh",
}


def require_node_console(config, attempts=3, interval=5):
    """Refuse an operation whose only transport cannot answer.

    The node's own console is the only transport this CLI uses; there is no
    DApp endpoint to fall back to, because a managed node may be on a private
    network and a public endpoint would at best hang.  An unreachable or
    lagging node therefore stops the operation here, where the reason reaches
    the operator, instead of inside a script whose output is kept in a private
    log.

    A node at the head of the chain still reports a lag as large as the gap
    between two masterchain blocks, so one reading above the tolerance is not
    evidence of a node left behind.  Read again before refusing, and say what
    was seen, so a single slow block cannot abort an election attempt.
    """
    # nodes imports this module, so the import stays inside the call.
    from . import nodes

    for attempt in range(max(1, attempts)):
        statistics = nodes.fetch_node_statistics(config)
        if nodes.is_node_synchronized(statistics):
            return
        if attempt + 1 < attempts:
            time.sleep(interval)
    raise process.OperationError(
        "Node is not synchronized and its console is the only transport "
        "available; run: evernode node sync -n "
        + config["name"]
        + " --wait (last reading: status "
        + str(statistics.get("node_status", statistics.get("sync_status")))
        + ", masterchain lag "
        + str(statistics.get("timediff"))
        + "s, shard lag "
        + str(statistics.get("shards_timediff"))
        + "s)"
    )


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


ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
# The reference scripts mark their own diagnostics with these prefixes.
SCRIPT_DIAGNOSTIC = re.compile(r"(###-ERROR|###-WARNING|\+\+\+-WARNING)")
# Twelve or more words in a row is a mnemonic, whatever line it appears on.
MNEMONIC = re.compile(r"(?:\b[a-z]{3,}\b[ \t]+){11,}\b[a-z]{3,}\b")
# There is no DApp mode, so a missing DApp credential is never the reason.
IRRELEVANT_DIAGNOSTIC = re.compile(r"project_id|access_key")


def script_diagnostics(result, limit=10):
    """Return the lines a failing script marked as its own diagnostics.

    Script output is kept off the terminal because a setup command can print a
    seed phrase, but then the one line that says what to do next - which
    transaction still needs signatures, which account is short of funds -
    reaches nobody. Pass on only the lines the script itself marked, without
    anything that could carry a phrase.
    """
    text = (result.stdout or "") + "\n" + (result.stderr or "")
    lines = []
    for line in text.splitlines():
        line = ANSI_ESCAPE.sub("", line).strip()
        if not SCRIPT_DIAGNOSTIC.search(line) or IRRELEVANT_DIAGNOSTIC.search(line):
            continue
        if "seed" in line.lower() or MNEMONIC.search(line):
            continue
        if line not in lines:
            lines.append(line)
    return lines[:limit]


def _record_script_failure(root, script, result):
    """Save a failed script's output privately and return its path."""
    directory = root / "logs" / "validator"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = script.replace("/", "-") + time.strftime("-failed-%Y%m%dT%H%M%SZ.log", time.gmtime())
    path = directory / name
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(result.stdout or "")
        stream.write(result.stderr or "")
    return path


def execute_validator_script(
    store, config, script, *arguments, timeout=300, sensitive=False, variables=None
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
    if script not in OFFLINE_SCRIPTS:
        require_node_console(config)
    environment = os.environ.copy()
    # These scripts otherwise reopen /dev/tty, which is unavailable in the
    # detached process session. The setup command has already collected approval.
    if script in ("DePool_deploy.sh", "stake_to_depool.sh"):
        environment["EVERNODE_APPROVED"] = "yes"
    # Only the names this module sets itself; never caller-supplied secrets.
    for name, value in (variables or {}).items():
        if name not in ("EVERNODE_INITIAL_STAKE",):
            raise process.OperationError(f"Unsupported script variable {name}")
        environment[name] = value
    result = process.execute_command(
        ["bash", path, *arguments],
        cwd=root / "scripts",
        env=environment,
        timeout=timeout,
        sensitive=sensitive,
        check=False,
    )
    if result.returncode:
        # These scripts print addresses, balances and contract state, which is
        # exactly what diagnosing a failure needs, but they are run as
        # sensitive so none of it reaches the terminal. Keep all of it in the
        # node's own private log, and report the lines the script marked as
        # diagnostics so the operator reads the reason without opening a file.
        path = _record_script_failure(root, script, result)
        reported = script_diagnostics(result)
        raise process.OperationError(
            f"{script} failed (exit {result.returncode})"
            + ("\n" + "\n".join(reported) if reported else "")
            + "\nFull output: "
            + str(path)
        )
    return result


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


def wait_for_active_account(store, config, address, label, timeout=240, interval=5):
    """Wait until a just-deployed contract is Active on chain.

    Deployment is one external message. The account becomes Active only once
    that message executes, so reporting the stage before then lets the next
    documented command run against an account that is still Uninit.
    """
    scripts_dir = _import_verification_context(store, config)
    program = """set -eo pipefail
# The reference scripts intentionally allow unset optional variables.
set +u
source ./env.sh
source ./functions.shinc
deadline=$(( $(date +%s) + EVERNODE_WAIT_SECONDS ))
while :; do
    read -r status balance _ <<< "$(Get_Account_Info "$EVERNODE_ACCOUNT")"
    if [[ $status == Active ]];then
        jq -cn --arg s "$status" --arg b "$balance" '{acc_type: $s, balance: $b}'
        exit 0
    fi
    if [[ $(date +%s) -ge $deadline ]];then
        printf "%s\\n" "$status" >&2
        exit 1
    fi
    sleep "$EVERNODE_WAIT_INTERVAL"
done
"""
    environment = os.environ.copy()
    environment["EVERNODE_ACCOUNT"] = address
    environment["EVERNODE_WAIT_SECONDS"] = str(timeout)
    environment["EVERNODE_WAIT_INTERVAL"] = str(interval)
    try:
        result = process.execute_command(
            ["bash", "-c", program],
            cwd=scripts_dir,
            env=environment,
            timeout=timeout + 60,
        )
    except process.OperationError as exc:
        raise process.OperationError(
            f"The {label} at {address} did not become active on chain within "
            f"{timeout}s. Check that it holds enough balance, then run this "
            "command again; a completed deployment is recognized and not repeated."
        ) from exc
    return json.loads(result.stdout.strip().splitlines()[-1])


def verify_imported_safe(store, config, wallet_address, timeout=300):
    """Read an imported Safe's on-chain custodian keys and threshold."""
    scripts_dir = _import_verification_context(store, config)
    program = """set -eo pipefail
# The reference scripts intentionally allow unset optional variables.
set +u
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


SOLO_STAKE_PRELUDE = r"""set -eo pipefail
# The reference scripts intentionally allow unset optional variables.
set +u
source ./env.sh
source ./functions.shinc
read -r elector_addr elector_type <<< "$(Get_Current_Elector_Type)"
wallet_hex=${EVERNODE_WALLET##*:}
Get_SC_current_state "$elector_addr" >/dev/null
elector_boc="${INPL_ELECTIONS_WORK_DIR}/${elector_addr##*:}.boc"
# What the elector owes this wallet from rounds that have already finished.
# compute_returned_stake takes the wallet as a number, so the address has to
# carry its 0x prefix; a bare hex string is rejected outright.
case $elector_type in
    fift)
        answer=$($CALL_CLI runget --boc "$elector_boc" \
            compute_returned_stake "0x${wallet_hex}" 2>&1 || true)
        recoverable=$(grep "Result:" <<< "$answer" | awk -F'"' '{print $2}')
        ;;
    solidity)
        argument=$(jq -cn --arg address "0x${wallet_hex}" '{wallet_addr: $address}')
        answer=$($CALL_CLI -j run --boc "$elector_boc" \
            compute_returned_stake "$argument" --abi "${INPL_Elector_ABI}" 2>&1 || true)
        recoverable=$(jq -r '.value0 // 0' <<< "$answer" 2>/dev/null || echo 0)
        ;;
    *)
        echoerr "###-ERROR: unknown elector type ${elector_type}"
        exit 1
        ;;
esac
# Owed nothing is the normal state, so an empty or absent answer is zero, not
# a failure that aborts the report.
[[ $recoverable =~ ^[0-9]+$ ]] || recoverable=0
"""

SOLO_STAKE_STATUS = r"""
elections_id="$(Get_Current_Elections_ID)" || elections_id=0
read -r wallet_status wallet_balance _ <<< "$(Get_Account_Info "$EVERNODE_WALLET")"
pending="$(Get_MSIG_Trans_List "$EVERNODE_WALLET")"
jq -cn --arg elector "$elector_addr" --arg type "$elector_type" \
    --arg election "${elections_id:-0}" --arg recoverable "$recoverable" \
    --arg status "$wallet_status" --arg balance "$wallet_balance" \
    --argjson pending "$pending" \
    '{elector: $elector, elector_type: $type, active_election_id: $election,
      recoverable_nanotokens: $recoverable, wallet_status: $status,
      wallet_balance_nanotokens: $balance,
      pending_elector_transactions:
        [$pending.transactions[] | select(.dest == $elector)]}'
"""

SOLO_STAKE_RECOVER = r"""
source ./managed_ops.shinc
# The signing handshake only runs when the wallet needs more than one
# signature, and the threshold is a property of the wallet, not of env.sh.
if ! custodians=$(Get_Account_Custodians_Info "$EVERNODE_WALLET");then
    echoerr "###-ERROR: could not read the wallet's custodians, so it is unknown how many signatures a recovery needs."
    exit 1
fi
read -r Total_Custodians Required_Signs <<< "$custodians"
if [[ ! $Required_Signs =~ ^[0-9]+$ ]] || [[ $Required_Signs -lt 1 ]];then
    echoerr "###-ERROR: the wallet reported no usable signature threshold (${custodians})."
    exit 1
fi
if [[ $recoverable -le 0 ]];then
    jq -cn '{recovered_nanotokens: "0", note: "The elector owes this wallet nothing"}'
    exit 0
fi
read -r _ wallet_before _ <<< "$(Get_Account_Info "$EVERNODE_WALLET")"
if ! $CALL_CONS -c "recover_stake ${INPL_ELECTIONS_WORK_DIR}/recover-query.boc";then
    echoerr "###-ERROR: the node could not build a stake recovery request."
    exit 1
fi
payload=$(base64 "${ELECTIONS_WORK_DIR}/recover-query.boc" | tr -d '[:space:]')
if [[ -z $payload ]];then
    echoerr "###-ERROR: the stake recovery request the node built is empty."
    exit 1
fi
request=$(jq -cn --arg dest "$elector_addr" --arg payload "$payload" \
    '{dest: $dest, value: 1000000000, bounce: true, allBalance: false,
      payload: $payload}')
if ! $CALL_CLI message --raw --output "${INPL_ELECTIONS_WORK_DIR}/recover-msg.boc" \
        --lifetime "$MSG_LIFETIME" \
        --sign "${INPL_KEYS_DIR}/${VALIDATOR_NAME}_1.keys.json" ${SIG_ID_OPTION} \
        --abi "$INPL_SafeC_Wallet_ABI" "$EVERNODE_WALLET" submitTransaction \
        "$request" >/dev/null;then
    echoerr "###-ERROR: could not build the wallet transaction for stake recovery."
    exit 1
fi
if ! trans_id=$(Submit_And_Identify "$EVERNODE_WALLET" "$elector_addr" \
        "${INPL_ELECTIONS_WORK_DIR}/recover-msg.boc" 1000000000 "$payload" \
        "${KEYS_DIR}/${VALIDATOR_NAME}_1.keys.json");then
    echoerr "###-ERROR: the stake recovery request did not reach the wallet."
    exit 1
fi
Complete_And_Await "$trans_id" "$EVERNODE_WALLET" "$VALIDATOR_NAME" \
    "Stake recovery" Effect_Account_Balance_At_Least "$EVERNODE_WALLET" \
    "$(( wallet_before + recoverable - 2000000000 ))" || exit 1
read -r _ wallet_after _ <<< "$(Get_Account_Info "$EVERNODE_WALLET")"
jq -cn --arg requested "$recoverable" --arg before "$wallet_before" \
    --arg after "$wallet_after" --arg id "$trans_id" \
    '{recovered_nanotokens: $requested, transaction_id: $id,
      wallet_balance_before_nanotokens: $before,
      wallet_balance_after_nanotokens: $after}'
"""


def read_solo_stake_state(store, config, wallet_address, timeout=300):
    """Read what the elector and the Safe say about a solo validator's stake.

    Every value is a getter or an account read, so this is a report: the stake
    the elector still owes this wallet, whether an election is open, the Safe's
    balance, and any transaction to the elector that is still unsigned.
    """
    scripts_dir = _import_verification_context(store, config)
    environment = os.environ.copy()
    environment["EVERNODE_WALLET"] = wallet_address
    result = process.execute_command(
        ["bash", "-c", SOLO_STAKE_PRELUDE + SOLO_STAKE_STATUS],
        cwd=scripts_dir,
        env=environment,
        timeout=timeout,
    )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise process.OperationError(
            "Could not read the elector and wallet state for this validator"
        ) from exc


def recover_solo_stake(store, config, wallet_address, timeout=900):
    """Ask the elector to return finished rounds, and see the balance rise.

    This is the same request the scheduled election preparation makes, sent on
    its own so it does not have to wait for an election window. It submits one
    transaction and signs it through against the wallet's own record of who has
    signed, exactly as every other operation does.
    """
    scripts_dir = _import_verification_context(store, config)
    environment = os.environ.copy()
    environment["EVERNODE_WALLET"] = wallet_address
    result = process.execute_command(
        ["bash", "-c", SOLO_STAKE_PRELUDE + SOLO_STAKE_RECOVER],
        cwd=scripts_dir,
        env=environment,
        timeout=timeout,
        sensitive=True,
        check=False,
    )
    if result.returncode:
        path = _record_script_failure(
            store.node_directory(config["name"]), "stake-recover", result
        )
        reported = script_diagnostics(result)
        raise process.OperationError(
            "Stake recovery did not complete"
            + ("\n" + "\n".join(reported) if reported else "")
            + "\nFull output: "
            + str(path)
        )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise process.OperationError("Could not read the recovery result") from exc


def read_depool_state(store, config, depool_address, wallet_address, timeout=300):
    """Read DePool settings, rounds, pending Safe transactions and own stake.

    Every value comes from a contract getter, so this is a read-only operator
    report. A participant that holds no stake is reported, not treated as an
    error, because that is the normal state after a completed withdrawal.
    """
    scripts_dir = _import_verification_context(store, config)
    program = """set -eo pipefail
# The reference scripts intentionally allow unset optional variables.
set +u
source ./env.sh
source ./functions.shinc
info="$(Get_DP_Info "$EVERNODE_DEPOOL")"
rounds="$(Get_DP_Rounds "$EVERNODE_DEPOOL")"
pending="$(Get_MSIG_Trans_List "$EVERNODE_WALLET")"
participants="$(Get_DP_Parts_List "$EVERNODE_DEPOOL")"
balance="$(Get_DP_Self_Balance "$EVERNODE_DEPOOL")"
if jq -e --arg wallet "$EVERNODE_WALLET" \
        '.participants | index($wallet) != null' <<< "$participants" >/dev/null; then
    participant="$(Get_DP_Part_Info "$EVERNODE_DEPOOL" "$EVERNODE_WALLET")"
else
    participant='null'
fi
jq -cn --argjson i "$info" --argjson r "$rounds" --argjson p "$pending" \
       --argjson s "$participant" --arg b "$balance" \
  '{depool: $i, rounds: $r, pending: $p, participant: $s, self_balance: $b}'
"""

    environment = os.environ.copy()
    environment["EVERNODE_DEPOOL"] = depool_address
    environment["EVERNODE_WALLET"] = wallet_address
    result = process.execute_command(
        ["bash", "-c", program], cwd=scripts_dir, env=environment, timeout=timeout
    )
    try:
        value = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise process.OperationError("Could not read DePool state") from exc
    rounds = value.get("rounds")
    rounds = list(rounds.get("rounds", {}).values()) if isinstance(rounds, dict) else []
    participant = value.get("participant")
    pending = value.get("pending")
    pending = pending.get("transactions", []) if isinstance(pending, dict) else []
    return {
        "pool_closed": value.get("depool", {}).get("poolClosed"),
        "validator_wallet": value.get("depool", {}).get("validatorWallet"),
        "self_balance_tokens": value.get("self_balance"),
        "participant_reward_fraction": value.get("depool", {}).get(
            "participantRewardFraction"
        ),
        "proxies": value.get("depool", {}).get("proxies"),
        "rounds": sorted(rounds, key=lambda item: int(item.get("id", 0))),
        "pending_wallet_transactions": pending,
        "own_stake": participant,
        "own_total_nanotokens": (
            participant.get("total") if isinstance(participant, dict) else None
        ),
        "own_pending_withdrawal_nanotokens": (
            participant.get("withdrawValue") if isinstance(participant, dict) else None
        ),
        "own_reinvest": (
            participant.get("reinvest") if isinstance(participant, dict) else None
        ),
    }


def verify_imported_depool(store, config, depool_address, timeout=300):
    """Read an imported DePool and check which wallet it uses."""
    scripts_dir = _import_verification_context(store, config)
    program = """set -eo pipefail
# The reference scripts intentionally allow unset optional variables.
set +u
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
