#!/usr/bin/env bash
# shellcheck disable=SC2155
# Denominator validators: keep the node's nominator pools moving, and bid from the pool whose turn it is.
#
#   denominator_cycle.sh cycle bid             what cron runs: maintenance, then a bid
#   denominator_cycle.sh cycle maintain        maintenance only: bidding stopped, or evernode stake recover
#   denominator_cycle.sh send POOL VALUE KIND [ARG]
#                                               one message from the Safe to one of the node's pools
#
# Maintenance does, for each pool, what the contract needs and nobody else sends:
#  - while its stake is out, it counts each new validator set (op 6), once that set's round has begun;
#  - once two are counted and the hold time has passed, it asks for the stake back, but only when the
#    elector owes at least what was sent, or a third set has been counted;
#  - once the pool is idle, it pays queued withdrawals (op 2): the pool refuses to bid while any wait.
# The bid is the kit's own take_part_in_elections.sh, unchanged, in its single-nominator mode against
# the pool: both contracts take the elector's request with the amount inserted after the query id. It
# runs from a directory that links every kit file but env.sh, whose env.sh points that mode at the pool.
#
# Only this node's own pools (DENOMINATOR_ADDRS) are ever addressed. Nothing here signs but the Safe,
# through the kit's own submit-and-sign functions.

set -o pipefail
set +u
SelfScriptName=$(basename "$0")
KIT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
if ! source "${KIT_DIR}/env.sh"; then
    echo "###-ERROR(${SelfScriptName}): Can't load env.sh" >&2
    exit 1
fi
source "${KIT_DIR}/functions.shinc"
source "${KIT_DIR}/managed_ops.shinc"

declare -ir NANO=1000000000
# pool.fc: what it keeps for storage, and the smallest stake it sends.
declare -ir POOL_RESERVE_nT=$(( 10 * NANO ))
declare -ir POOL_MIN_STAKE_nT=$(( 500 * NANO ))
# pool.fc's validator-funds floor when the network has no config 40, as on Everscale mainnet.
declare -ir POOL_PUNISHMENT_FLOOR_nT=$(( 101 * NANO ))
# Left in the pool beyond its reserve when bidding, so the bid never meets the reserve check.
declare -ir BID_MARGIN_nT=$(( 1 * NANO ))
# Queued withdrawals paid per op 2, and what each costs at masterchain prices (about 0.09 token).
declare -ir PAY_LIMIT=40
declare -ir PAY_EACH_nT=150000000
declare -i OP_FEE_nT=${DENOMINATOR_OP_FEE:-2000000000}
WORK="${ELECTIONS_WORK_DIR}/denominator"
INPL_WORK="${INPL_ELECTIONS_WORK_DIR}/denominator"
LABELS=(A B)

function Usage() {
    echoerr "usage: ${SelfScriptName} cycle bid|maintain | send POOL VALUE KIND [ARG]"
    exit 1
}

function Require_Setup() {
    if [[ -z ${DENOMINATOR_ADDRS:-} ]];then
        echoerr "###-ERROR(${SelfScriptName}): DENOMINATOR_ADDRS is not set in env.sh. Run: evernode denominator prepare -n ${VALIDATOR_NAME}"
        exit 1
    fi
    if [[ -z ${DENOMINATOR_TOOLS_IMAGE:-} ]] || ! docker image inspect "$DENOMINATOR_TOOLS_IMAGE" >/dev/null 2>&1;then
        echoerr "###-ERROR(${SelfScriptName}): the pool tools image ${DENOMINATOR_TOOLS_IMAGE:-} is not on this host (an image prune removes it). Run: evernode denominator prepare -n ${VALIDATOR_NAME}"
        exit 1
    fi
    Safe_addr="$(cat "${KEYS_DIR}/${VALIDATOR_NAME}.addr" 2>/dev/null | tr '[:upper:]' '[:lower:]')"
    if [[ ! $Safe_addr =~ ^-1:[0-9a-f]{64}$ ]];then
        echoerr "###-ERROR(${SelfScriptName}): this node's Safe address is missing or not in the masterchain (${Safe_addr})."
        exit 1
    fi
    # Private, as evernode keeps it: the node's console writes here as the same user.
    [[ -d $WORK ]] || mkdir -p -m 700 "$WORK"
}

function Pool_Label() {
    local i=0 pool
    for pool in $DENOMINATOR_ADDRS; do
        [[ $pool == "$1" ]] && { echo "${LABELS[$i]:-$i}"; return 0; }
        i=$(( i + 1 ))
    done
    return 1
}

# Run one command line in the pool tools image, with the work directory at /work.
function Pool_Tool() {
    docker run --rm --network none -v "${WORK}:/work" -w /opt/nominator "$DENOMINATOR_TOOLS_IMAGE" "$1"
}

# The pool's account, decoded by its own tool: JSON with .pool holding pool.fc's storage.
function Pool_Read() {
    local pool="$1" file="pool_${1##*:}.boc" out
    rm -f "${WORK}/${file}"
    if ! $CALL_CONS -c "getaccountstate ${pool} ${INPL_WORK}/${file}" >/dev/null 2>&1 || [[ ! -s "${WORK}/${file}" ]];then
        echoerr "###-ERROR: the node could not read pool ${pool}."
        return 1
    fi
    if ! out="$(Pool_Tool "node tools/cells.cjs account /work/${file}" 2>/dev/null | tail -n 1)" \
            || [[ -z "$(jq -r '.pool.state // empty' <<< "$out" 2>/dev/null)" ]];then
        echoerr "###-ERROR: pool ${pool} is $(jq -r '.state // "unreadable"' <<< "$out" 2>/dev/null), or does not run the pinned pool code."
        return 1
    fi
    if [[ "$(jq -r '.validator' <<< "$out")" != "$Safe_addr" ]];then
        echoerr "###-ERROR: pool ${pool} takes instructions from $(jq -r '.validator' <<< "$out"), not from this node's Safe ${Safe_addr}."
        return 1
    fi
    echo "$out"
}

# Build one body with the tools image into the work directory; print its file name.
function Pool_Body() {
    local file="body-$(date +%s)-$$.boc"
    Pool_Tool "node tools/cells.cjs body ${1} /work/${file} ${2:-}" >/dev/null || return 1
    echo "$file"
}

# A validator withdrawal has landed: the pool's books show the validator's funds at most CEILING.
# The Safe's balance cannot show it, because the Safe sends a token and gets the amount back.
# Input: pool, ceiling in nanotokens
function Effect_Pool_Validator_Funds_At_Most() {
    local acc funds
    acc=$(Pool_Read "$1" 2>/dev/null) || return 2
    funds=$(jq -r '.pool.validatorAmount' <<< "$acc")
    [[ $funds =~ ^[0-9]+$ ]] || return 2
    [[ $funds -le $2 ]]
}

# One message from the Safe to a pool, signed through as every Safe operation is.
# Input: pool, value in nanotokens, body file name in the work directory, label, and optionally the
#        effect that shows it landed (default: the Safe's balance fell, the value having left it)
# Output: JSON with the Safe's transaction id and its balance before and after
function Pool_Send() {
    local pool="$1" value="$2" body="$3" label="$4"
    shift 4
    local effect=("$@")
    local payload custodians info wallet_status wallet_before wallet_after="" request trans_id
    payload="$(base64 < "${WORK}/${body}" | tr -d '[:space:]')"
    if [[ -z $payload ]];then
        echoerr "###-ERROR: ${label}: the message body is empty, so nothing was sent."
        return 1
    fi
    if ! custodians=$(Get_Account_Custodians_Info "$Safe_addr");then
        echoerr "###-ERROR: ${label}: could not read the Safe's custodians, so nothing was sent."
        return 1
    fi
    read -r _ Required_Signs <<< "$custodians"
    if [[ ! $Required_Signs =~ ^[1-9][0-9]*$ ]];then
        echoerr "###-ERROR: ${label}: the Safe reported no usable signature threshold (${custodians})."
        return 1
    fi
    if ! info=$(Get_Account_Info "$Safe_addr");then
        echoerr "###-ERROR: ${label}: could not read the Safe, so nothing was sent."
        return 1
    fi
    read -r wallet_status wallet_before _ <<< "$info"
    if [[ $wallet_status != Active ]] || [[ ! $wallet_before =~ ^[0-9]+$ ]] \
            || [[ $wallet_before -lt $(( value + (Required_Signs + 1) * NANO )) ]];then
        echoerr "###-ERROR: ${label}: the Safe holds $(( ${wallet_before:-0} / NANO )) tokens, too few to send $(( value / NANO )) and pay for ${Required_Signs} signature(s). Top it up."
        return 1
    fi
    request=$(jq -cn --arg dest "$pool" --argjson value "$value" --arg payload "$payload" \
        '{dest: $dest, value: $value, bounce: true, allBalance: false, payload: $payload}')
    if ! $CALL_CLI message --raw --output "${INPL_WORK}/send-msg.boc" --lifetime "$MSG_LIFETIME" \
            --sign "${INPL_KEYS_DIR}/${VALIDATOR_NAME}_1.keys.json" ${SIG_ID_OPTION} \
            --abi "$INPL_SafeC_Wallet_ABI" "$Safe_addr" submitTransaction "$request" >/dev/null;then
        echoerr "###-ERROR: ${label}: could not build the Safe transaction, so nothing was sent."
        return 1
    fi
    local key_file="${KEYS_DIR}/${VALIDATOR_NAME}_1.keys.json"
    if [[ $Required_Signs -gt 1 ]];then
        Require_Queue_Space "$Safe_addr" "$key_file" || return 1
    fi
    if ! trans_id=$(Submit_And_Identify "$Safe_addr" "$pool" "${INPL_WORK}/send-msg.boc" \
            "$value" "$payload" "$key_file" true);then
        echoerr "###-ERROR: ${label}: the message did not reach the Safe."
        return 1
    fi
    [[ ${#effect[@]} -gt 0 ]] || effect=(Effect_Account_Balance_At_Most "$Safe_addr" "$(( wallet_before - 1 ))")
    if ! Complete_And_Await "$trans_id" "$Safe_addr" "$VALIDATOR_NAME" "$label" "${effect[@]}";then
        return 1
    fi
    if info=$(Get_Account_Info "$Safe_addr");then
        read -r _ wallet_after _ <<< "$info"
    fi
    jq -cn --arg id "${trans_id:-}" --arg before "$wallet_before" --arg after "$wallet_after" \
        '{transaction_id: $id, wallet_balance_before_nanotokens: $before,
          wallet_balance_after_nanotokens: $after}'
}

# Transactions the Safe still holds unsigned for this pool: a new one would send it twice.
function Pending_To() {
    local list
    list="$(Get_MSIG_Trans_List "$Safe_addr" 2>/dev/null)" || { echo "unknown"; return 0; }
    jq -r --arg dest "$1" '[.transactions[]? | select(.dest == $dest)] | length' <<< "$list" 2>/dev/null || echo "unknown"
}

# What one pool needs now; sends it and prints a JSON line saying what was done.
function Pool_Maintain() {
    local pool="$1" label="$2" acc state changes change_time held sent requests owed pending
    local now action="none" reason="" body result
    if ! acc=$(Pool_Read "$pool");then
        jq -cn --arg pool "$pool" --arg label "$label" '{pool: $pool, label: $label, action: "none", error: "unreadable"}'
        return 1
    fi
    state=$(jq -r '.pool.state' <<< "$acc")
    changes=$(jq -r '.pool.validatorSetChanges' <<< "$acc")
    change_time=$(jq -r '.pool.validatorSetChangeTime' <<< "$acc")
    held=$(jq -r '.pool.stakeHeldFor' <<< "$acc")
    sent=$(jq -r '.pool.stakeAmountSent' <<< "$acc")
    requests=$(jq -r '.pool.withdrawRequests' <<< "$acc")
    now=$(date +%s)
    pending=$(Pending_To "$pool")
    if [[ $pending != 0 ]];then
        reason="the Safe holds ${pending} unsigned transaction(s) to this pool; finish them with evernode stake confirm"
    elif [[ $state -ne 0 ]];then
        local p34_since
        p34_since=$($CALL_CONS -jc 'getconfig 34' 2>/dev/null | jq -r '.p34.utime_since // empty' 2>/dev/null)
        if [[ $changes -lt 3 ]] && [[ $p34_since =~ ^[0-9]+$ ]] && [[ $p34_since -gt $change_time ]];then
            action="update"
        elif [[ $changes -ge 2 ]] && { [[ $changes -gt 2 ]] || [[ $(( now - change_time )) -gt $(( held + 60 )) ]]; };then
            owed=$(Elector_Owes "$pool") || owed=0
            if [[ $owed -gt 0 ]] && { [[ $owed -ge $sent ]] || [[ $changes -gt 2 ]]; };then
                action="recover"
            else
                reason="the elector owes ${owed} nanotokens of the ${sent} sent; waiting for the round to be released"
            fi
        else
            reason="stake out; ${changes} new validator set(s) counted"
        fi
    elif [[ $requests -gt 0 ]];then
        action="pay"
    fi
    case $action in
        update)
            body=$(Pool_Body update "$now") && result=$(Pool_Send "$pool" "$NANO" "$body" "Counting the new validator set at pool ${label}") || action="update failed" ;;
        recover)
            body=$(Pool_Body recover "$now") && result=$(Pool_Send "$pool" "$OP_FEE_nT" "$body" "Recovering pool ${label}'s stake") || action="recover failed" ;;
        pay)
            local n=$(( requests < PAY_LIMIT ? requests : PAY_LIMIT ))
            body=$(Pool_Body pay "$n $now") && result=$(Pool_Send "$pool" "$(( NANO + n * PAY_EACH_nT ))" "$body" "Paying ${n} queued withdrawal(s) at pool ${label}") || action="pay failed" ;;
    esac
    jq -cn --arg pool "$pool" --arg label "$label" --argjson state "$state" --arg action "$action" \
        --arg reason "$reason" --argjson requests "$requests" --argjson changes "$changes" \
        '{pool: $pool, label: $label, state: $state, action: $action, reason: $reason,
          withdraw_requests: $requests, validator_set_changes: $changes}'
    [[ $action != *failed ]]
}

# Bid from the pool whose turn it is, through the kit's own bid script.
function Pool_Bid() {
    local elections_id min_stake pool label acc state stake_at requests owed balance validator_funds
    local floor minimum available chosen="" chosen_label stake_tokens reasons=()
    if ! elections_id=$(Get_Current_Elections_ID) || [[ ! $elections_id =~ ^[0-9]+$ ]];then
        echoerr "###-ERROR: could not read the open election from the elector."
        return 1
    fi
    if [[ $elections_id -eq 0 ]];then
        echo "---INFO: There is no elections now! Nothing to do!"
        return 0
    fi
    min_stake=$($CALL_CONS -jc 'getconfig 17' 2>/dev/null | jq -r '.p17.min_stake_dec // empty' 2>/dev/null)
    [[ $min_stake =~ ^[0-9]+$ ]] || min_stake=0
    for pool in $DENOMINATOR_ADDRS; do
        label=$(Pool_Label "$pool")
        acc=$(Pool_Read "$pool") || { reasons+=("pool ${label}: unreadable"); continue; }
        state=$(jq -r '.pool.state' <<< "$acc")
        stake_at=$(jq -r '.pool.stakeAt' <<< "$acc")
        if [[ $state -ne 0 ]] && [[ $stake_at -eq $elections_id ]];then
            echo "---INFO: pool ${label} (${pool}) already participated in these elections (${elections_id})"
            return 0
        fi
        [[ -n $chosen ]] && continue
        requests=$(jq -r '.pool.withdrawRequests' <<< "$acc")
        balance=$(jq -r '.balance' <<< "$acc")
        validator_funds=$(jq -r '.pool.validatorAmount' <<< "$acc")
        floor=$(jq -r '.pool.config.minValidatorStake' <<< "$acc")
        [[ $floor -ge $POOL_PUNISHMENT_FLOOR_nT ]] || floor=$POOL_PUNISHMENT_FLOOR_nT
        minimum=$(( min_stake > POOL_MIN_STAKE_nT ? min_stake : POOL_MIN_STAKE_nT ))
        available=$(( balance - POOL_RESERVE_nT - BID_MARGIN_nT ))
        if [[ $state -ne 0 ]];then
            reasons+=("pool ${label}: its stake is out")
        elif [[ $requests -gt 0 ]];then
            reasons+=("pool ${label}: ${requests} queued withdrawal(s) to pay first")
        elif ! owed=$(Elector_Owes "$pool") || [[ $owed -gt 0 ]];then
            reasons+=("pool ${label}: the elector still owes it ${owed:-an unknown amount}; recover it first")
        elif [[ $validator_funds -lt $floor ]];then
            reasons+=("pool ${label}: the validator's own funds are $(( validator_funds / NANO )) tokens, below the $(( floor / NANO )) it needs; run evernode denominator deposit")
        elif [[ $available -lt $minimum ]];then
            reasons+=("pool ${label}: it can stake $(( available / NANO )) tokens, below the $(( minimum / NANO )) the election needs")
        else
            chosen=$pool
            chosen_label=$label
            stake_tokens=$(( available / NANO ))
        fi
    done
    if [[ -z $chosen ]];then
        echo "---INFO: no pool bids in elections ${elections_id}: ${reasons[*]}"
        return 0
    fi
    echo "---INFO: pool ${chosen_label} (${chosen}) bids ${stake_tokens} tokens in elections ${elections_id}"
    local view="${WORK}/bid-${chosen_label}"
    rm -rf "$view" && mkdir -p "$view" || return 1
    local item
    for item in "${KIT_DIR}"/*; do
        [[ $(basename "$item") == env.sh ]] || ln -s "$item" "${view}/$(basename "$item")"
    done
    {
        printf 'source %q\n' "${KIT_DIR}/env.sh"
        printf 'export STAKE_MODE=nominator\n'
        printf 'export NOMINATOR_ADDR=%q\n' "$chosen"
        printf 'export NOMINATOR_TOOLS_IMAGE=%q\n' "$DENOMINATOR_TOOLS_IMAGE"
        printf 'export NOMINATOR_RESERVE=%q\n' "$(( POOL_RESERVE_nT + BID_MARGIN_nT ))"
        printf 'export NOMINATOR_OP_FEE=%q\n' "$OP_FEE_nT"
        printf 'export MSIG_FIX_STAKE=%q\n' "$stake_tokens"
    } > "${view}/env.sh"
    local status=0
    "${view}/take_part_in_elections.sh" || status=$?
    # Links, and an env.sh that names no secret: nothing of the bid is kept in them.
    rm -rf "$view"
    return $status
}

case "${1:-}" in
    cycle)
        mode="${2:-}"
        [[ $mode == bid || $mode == maintain ]] || Usage
        Require_Setup
        Require_Node_Console "${NODE_SYNC_TOLERANCE:-20}" || exit 1
        acted=false failed=false lines=()
        for pool in $DENOMINATOR_ADDRS; do
            line=$(Pool_Maintain "$pool" "$(Pool_Label "$pool")") || failed=true
            echo "---INFO: maintenance: ${line}"
            lines+=("$line")
            [[ "$(jq -r '.action' <<< "$line")" =~ ^(update|recover|pay)$ ]] && acted=true
        done
        if [[ $mode == maintain ]];then
            printf '%s\n' "${lines[@]}" | jq -cs '{pools: .}'
            $failed && exit 1
            exit 0
        fi
        # What maintenance sent lands within seconds; the bid reads the pools after it.
        $acted && sleep "${DENOMINATOR_SETTLE_SECONDS:-30}"
        Pool_Bid
        ;;
    send)
        [[ $# -ge 4 ]] || Usage
        Require_Setup
        Require_Node_Console "${NODE_SYNC_TOLERANCE:-20}" >&2 || exit 1
        pool="$(tr '[:upper:]' '[:lower:]' <<< "$2")"
        label=$(Pool_Label "$pool") || { echoerr "###-ERROR: ${pool} is not one of this node's pools."; exit 1; }
        [[ $3 =~ ^[1-9][0-9]*$ ]] || Usage
        case $4 in
            deposit-validator|withdraw-validator) ;;
            *) echoerr "###-ERROR: the Safe sends a pool only deposit-validator or withdraw-validator."; exit 1 ;;
        esac
        acc=$(Pool_Read "$pool") || exit 1
        effect=()
        if [[ $4 == withdraw-validator ]];then
            [[ ${5:-} =~ ^[1-9][0-9]*$ ]] || Usage
            funds=$(jq -r '.pool.validatorAmount' <<< "$acc")
            effect=(Effect_Pool_Validator_Funds_At_Most "$pool" "$(( funds > $5 ? funds - $5 : 0 ))")
        fi
        body=$(Pool_Body "$4" "${5:-} $(date +%s)") || { echoerr "###-ERROR: could not build the message body."; exit 1; }
        Pool_Send "$pool" "$3" "$body" "Pool ${label}: ${4}" "${effect[@]}" || exit 1
        ;;
    *)
        Usage
        ;;
esac
