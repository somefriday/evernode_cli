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

####################
SLEEP_TIMEOUT=10
SEND_ATTEMPTS=10
###################
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
# Check if the node is running and set DAPP mode accordingly
if pgrep ${NODE_BIN_NAME} > /dev/null; then
    echo "---WARNING(${SelfScriptName} line $LINENO): Node is running. "
    # If the access mode is set to console and the node is not synchronized, prompt the user to switch to dapp mode temporarily
    if Ask_to_switch_to_dapp;then
        echo "Proceeding..."
        export FORCE_USE_DAPP=true
    elif [[ $? -gt 1 ]];then
        echo "Cancelled."
        exit 1
    fi
else
    echo "---WARNING(${SelfScriptName} line $LINENO):Node is NOT running. Set DAPP mode."
    export FORCE_USE_DAPP=true
fi

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
# Get and check Transaction ID to sign
Trans_List="$(Get_MSIG_Trans_List "${MSIG_ADDR}")"
Trans_QTY=$(echo "$Trans_List" | jq -r ".transactions|length")
Trans_QTY=$((Trans_QTY))
if [[ $Trans_QTY -eq 0 ]];then
    echo
    echo "###-ERROR(${SelfScriptName} line $LINENO): Trans_QTY=$Trans_QTY. NO transactions to sign. Exit."
    echo
    exit 0
fi
if [[ $Trans_QTY -gt 1 ]] && [[ -z ${TrID_force} ]];then
    echo "$Trans_List"
    echo
    echo "###-ERROR(${SelfScriptName} line $LINENO): Trans_QTY=$Trans_QTY. Multi transaction bulk signing not allowed!"
    echo "To force confirm one transaction, use './Sign_Trans.sh AccName TransID' "
    echo
    exit 1
fi

Trans_ID=$(echo "$Trans_List" | jq -r '.transactions[].id')
if [[ -z ${Trans_ID} ]] || [[ "${Trans_ID}" == "0" ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Error getting transaction ID: '${Trans_ID}'. Exit."
    echo
    exit 1
fi

if [[ -n ${TrID_force} ]];then
    Trans_ID=${TrID_force}
    echo "Set TransID to ${Trans_ID}"
fi

if ! echo "$Trans_List" | grep -q "${Trans_ID}";then
    echo
    echo "###-ERROR(${SelfScriptName} line $LINENO): Transaction # ${Trans_ID} not found in list. Exit."
    echo
    exit 1
fi
echo "Found $Trans_QTY transaction. Will sign transaction with ID: $Trans_ID"

##############################################################################
# Get Required number of confirmations
Confirms_QTY=$(echo "$Trans_List" | jq -r ".transactions[]|select(.id == \"$Trans_ID\")|.signsRequired")
Confirms_QTY=$((Confirms_QTY))
# Get Received number of confirmations
Conf_Recv_QTY=$(echo "$Trans_List" | jq -r ".transactions[]|select(.id == \"$Trans_ID\")|.signsReceived")
Conf_Recv_QTY=$((Conf_Recv_QTY))

echo "Required number of confirmations: $Confirms_QTY. Received confirmations: $Conf_Recv_QTY"
echo "******************************"

##############################################################################
# Send signatures one by one with checks 
# Assume that transaction was made and already signed by custodian with pubkey index # 0x0
# other custodians has keys in files in ${KEYS_DIR}
Confirmed_Flag=false
for (( i=$((Conf_Recv_QTY + 1)); i <= Confirms_QTY; i++ )); do
    Signed_Flag=false
    for (( Attempts_to_send=1;  Attempts_to_send <= SEND_ATTEMPTS;  Attempts_to_send++ )); do
        #======================================================================
        # Send confirmations signature
        echo "Try #${Attempts_to_send} to send confirmations signature #${i} from file ${KeyFileName}_${i}.keys.json"
        Send_MSIG_Trans_Confirmation "${Trans_ID}" "${MSIG_ADDR}" "${KeyFileName}_${i}.keys.json" # All keys files in ${KEYS_DIR} only
        sleep $SLEEP_TIMEOUT
        Trans_List="$(Get_MSIG_Trans_List "${MSIG_ADDR}")"
        CurrTransInfo=$(echo "${Trans_List}" | jq ".transactions[]|select(.id == \"$Trans_ID\")")
        if [[ -z ${CurrTransInfo} ]];then
            echo "\$\$\$-SUCCESS: Transaction # $Trans_ID signed and send"
            Confirmed_Flag=true
            Signed_Flag=true
            break
        fi
        RcvQTY=$(echo "${CurrTransInfo}" | jq -r ".signsReceived")
        RcvQTY=$((RcvQTY))
        if [[ $RcvQTY -gt $Conf_Recv_QTY ]];then
            echo "Signing transaction $Trans_ID by custodian ${i} was done SUCCESSFULLY!"
            echo
            Conf_Recv_QTY=$RcvQTY
            Signed_Flag=true
            break
        fi
        echo "###-ERROR(${SelfScriptName} line $LINENO): Confirmation try # ${i} FAILED!!! Will try again..."
        echo
    done
    ########################################
    if ! $Signed_Flag;then
        echo "###-ERROR(${SelfScriptName} line $LINENO): CANNOT sign transaction $Trans_ID by key # ${i} from file: ${KEYS_DIR}/${KeyFileName}_${i}.keys.json"
    fi
    #======================================================================
    # Chech transaction signed and leaved
    if $Confirmed_Flag;then
        echo "\$\$\$-SUCCESS: Transaction # $Trans_ID signed and send"
        break
    fi
done

if  ! $Confirmed_Flag;then
    echo "###-ERROR(${SelfScriptName} line $LINENO): CANNOT sign transaction $Trans_ID by key # ${i} from file: ${KEYS_DIR}/${KeyFileName}_${i}.keys.json"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_SOS_sign ALARM!!! Signing transaction $Trans_ID for election FAILED!!!" > /dev/null 2>&1
    exit 1
fi

# Send_msg_toTelBot "$VALIDATOR_NAME Server" "Transaction $Trans_ID for election confirmed." /dev/null 2>&1

echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "================================================================================================"

exit 0
