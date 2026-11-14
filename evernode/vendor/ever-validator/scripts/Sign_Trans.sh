#!/usr/bin/env bash
# shellcheck disable=SC2031

# Copyright (C) 2019-2025 EverX

# Disclaimer
##################################################################################################################
# Your use of this script/function indicates your acceptance of the following terms. 
# The author(s) of this script/function shall not be held liable for any damage that its use may cause to your systems. 
# This script/function is provided 'AS IS', without warranty of any kind. 
# The entire risk as to the quality and performance of the script/function is with you. 
# Should the script/function prove defective, you assume the cost of all necessary servicing, repair, or correction.
# In no event will the author(s) be liable for any damages whatsoever including, but not limited to, 
# loss of business profits, business interruption, loss of business information, 
# or other pecuniary loss arising out of the use or inability to use the script/function. 
# This script/function, including any modifications and derivatives, is licensed to you under the terms of the GPL-3.0 license. 
# You are free to modify, distribute, and convey this script/function and its derivatives under the same license, 
# provided that you also make the source code available under GPL-3.0. 
# This disclaimer does not intend to restrict the rights granted by the GPL-3.0 license, 
# including but not limited to the rights to use, modify, and distribute the script/function and its derivatives.
# The author(s) reserve the right to change this disclaimer at any time.
##################################################################################################################

# ------------------------------------------------------------------------
# Script assumes that: 
#   - all keypairs are in ${KEYS_DIR} folder
#
#   use: Sign_Trans.sh [AccName] [TransactionID]
#      AccName - filename of separate acc with AccName_{n}.keys.json keys files
#      If AccName omitted - will use ${VALIDATOR_NAME}.addr & ${VALIDATOR_NAME}_{n}.keys.json
#
#  To force sign one of few transaction for specified acc
#   use: Sign_Trans.sh [AccName] [TransactionID]
# ------------------------------------------------------------------------

# The signing budget is no longer a count of attempts: it is the transaction's
# own expiry, read from its id and the wallet's own lifetime.
function sgn_usage(){
echo
echo " use: Sign_Trans.sh [AccName]"
echo " AccName - filename of separate acc with AccName_{n}.keys.json keys files"
echo " If AccName omitted - will use $VALIDATOR_NAME.addr and ${VALIDATOR_NAME}_{n}.keys.json"
echo
exit 0
}
echo
echo "######################################## Signing script ########################################"
SelfScriptName=$(basename "$0") && export SelfScriptName
echo "INFO: $SelfScriptName BEGIN $(date +%s) / $(date +'%F %T %Z')"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
if ! source "${SCRIPT_DIR}/env.sh"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't load env.sh"
    exit 1
fi
#================================================================
# Set atomic lock
LOCK_FILE="${ELECTIONS_WORK_DIR}/${SelfScriptName}.lock"
# Get file descriptor for lock
exec 200>"$LOCK_FILE"
# Try to get lock
if ! flock -n 200; then
    printf "\e[31;1m%b\e[0m\n" "###-ERROR(${SelfScriptName} line $LINENO): Script already running. Exiting..."
    exit 1
fi
# Release lock on exit
trap 'flock -u 200' EXIT
#================================================================

#=====================================================
# Check utilities is installed
if ! command -v yq &>/dev/null; then echo "###-ERROR(${SelfScriptName} line $LINENO): 'yq' is not installed. Please install it and run the script again."; exit 1; fi
if ! command -v jq &>/dev/null; then echo "###-ERROR(${SelfScriptName} line $LINENO): 'jq' is not installed. Please install it and run the script again."; exit 1; fi
if ! command -v bc &>/dev/null; then echo "###-ERROR(${SelfScriptName} line $LINENO): 'bc' is not installed. Please install it and run the script again."; exit 1; fi

source "${SCRIPT_DIR}/functions.shinc"

echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo

#==================================================
# The node's own console is the only transport, so what matters is whether the
# node answers it - not whether a node process happens to be visible in this
# process table, which it is not when the scripts run outside the container.
Require_Node_Console || exit 1

#==================================================
# Acc by name or default 
AccName=$1
if [[ -z $AccName ]];then
    MSIG_ADDR="$(cat "${KEYS_DIR}/${VALIDATOR_NAME}.addr")"
    KeyFileName="$VALIDATOR_NAME"
    if [[ -z $MSIG_ADDR ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find ${KEYS_DIR}/${VALIDATOR_NAME}.addr" && sgn_usage
        exit 1
    fi
else
    MSIG_ADDR="$(cat "${KEYS_DIR}/${AccName}.addr")"
    KeyFileName="${AccName}"
    if [[ -z $MSIG_ADDR ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find ${KEYS_DIR}/${AccName}.addr" && sgn_usage
        exit 1
    fi
fi
#=================================================
# Set TransID if specified
TrID_force=$2
[[ -n ${TrID_force} ]] && echo -e "${RedBack}${BoldText}Forced to confirm transaction #: ${TrID_force}${NormText}"

#=================================================
# 
workchain=$(echo "${MSIG_ADDR}" | cut -d ':' -f 1)
echo "MSIG_ADDR = ${MSIG_ADDR}"
echo "WorkChain:  $workchain"

##############################################################################
# Resolve exactly which transaction to sign.
#
# The id is looked up as an id, not searched for as text: every entry carries a
# 77-digit creator, so a truncated or coincidentally embedded id would pass a
# textual search, and a confirmation for an id this wallet does not hold is
# rejected before tvm.accept() - leaving nothing in any block to explain why
# the command failed.
Trans_List="$(Get_MSIG_Trans_List "${MSIG_ADDR}")"
Trans_QTY=$(echo "$Trans_List" | jq -r ".transactions|length")
Trans_QTY=$((Trans_QTY))
if [[ -z ${TrID_force} ]];then
    if [[ $Trans_QTY -eq 0 ]];then
        echoerr "###-ERROR(${SelfScriptName} line $LINENO): wallet ${MSIG_ADDR} holds no pending transaction, so there is nothing to sign."
        exit 1
    fi
    if [[ $Trans_QTY -gt 1 ]];then
        echo "$Trans_List" | jq
        echoerr "###-ERROR(${SelfScriptName} line $LINENO): the wallet holds ${Trans_QTY} pending transactions and signing one executes the transfer it carries, so the id has to be named: './Sign_Trans.sh ${KeyFileName} <TransID>'"
        exit 1
    fi
    Trans_ID=$(echo "$Trans_List" | jq -r '.transactions[0].id')
else
    Trans_ID="${TrID_force}"
fi
if [[ ! $Trans_ID =~ ^[0-9]+$ ]];then
    echoerr "###-ERROR(${SelfScriptName} line $LINENO): '${Trans_ID}' is not a transaction id."
    exit 1
fi

Trans_Entry="$(Get_Pending_Entry "${MSIG_ADDR}" "${Trans_ID}")"
if [[ $? -ne 0 ]];then
    echoerr "###-ERROR(${SelfScriptName} line $LINENO): the wallet ${MSIG_ADDR} could not be read, so it is unknown whether it holds transaction ${Trans_ID}. Nothing was sent."
    exit 1
fi
if [[ -z $Trans_Entry ]];then
    echoerr "###-ERROR(${SelfScriptName} line $LINENO): wallet ${MSIG_ADDR} does not hold a pending transaction ${Trans_ID}. It may have executed already, or expired - this wallet keeps a pending transaction for $(Get_MSIG_Expiration_Time "${MSIG_ADDR}")s. Read the current list with 'evernode depool info'. Nothing was sent."
    exit 1
fi

Trans_Dest=$(jq -r '.dest' <<< "$Trans_Entry")
Trans_Value=$(jq -r '.value' <<< "$Trans_Entry")
Confirms_QTY=$(jq -r '.signsRequired' <<< "$Trans_Entry")
Conf_Recv_QTY=$(jq -r '.signsReceived' <<< "$Trans_Entry")
echo "Transaction ${Trans_ID}: ${Trans_Value} nanotokens to ${Trans_Dest}, holding ${Conf_Recv_QTY} of ${Confirms_QTY} required signatures."
echo "******************************"

##############################################################################
# Sign it through to execution.
#
# The deadline is the transaction's own expiry, which is computable: the id
# carries the second it was created in its high 32 bits and the wallet reports
# how long it keeps a pending transaction. Staying strictly inside that window
# is what lets the entry's disappearance mean "it executed" rather than "it
# expired", and it replaces a fixed attempt budget that used to give up with
# most of the transaction's valid life still ahead of it.
declare -i Now=$(date +%s)
declare -i Sign_Deadline=$(( Now + 2 * ${MSG_LIFETIME:-60} ))
Expires_At="$(Trans_Expires_At "${MSIG_ADDR}" "${Trans_ID}")"
if [[ $Expires_At =~ ^[0-9]+$ ]];then
    if [[ $Expires_At -le $Now ]];then
        echoerr "###-ERROR(${SelfScriptName} line $LINENO): transaction ${Trans_ID} expired at $(TD_unix2human "$Expires_At") and can no longer be signed. Nothing was sent."
        exit 1
    fi
    # Stop short of the expiry itself, so a verdict is never read in the second
    # the transaction is being removed.
    if [[ $(( Expires_At - 5 )) -lt $Sign_Deadline ]];then
        Sign_Deadline=$(( Expires_At - 5 ))
    fi
    echo "---INFO: transaction ${Trans_ID} is signable until $(TD_unix2human "$Expires_At")."
fi

if ! Sign_To_Execution "${MSIG_ADDR}" "${KeyFileName}" "${Trans_ID}" "${Sign_Deadline}" \
        "Transaction ${Trans_ID}";then
    Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_SOS_sign ALARM!!! Signing transaction $Trans_ID FAILED!!!" > /dev/null 2>&1
    exit 1
fi

echo "\$\$\$-SUCCESS: transaction ${Trans_ID} executed; ${Trans_Value} nanotokens were sent to ${Trans_Dest}."
echo "---INFO: the transfer is an internal message the destination can still refuse. Check the result with 'evernode depool info'."
echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "================================================================================================"

exit 0
