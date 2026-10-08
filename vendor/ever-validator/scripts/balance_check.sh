#!/usr/bin/env bash
# shellcheck disable=SC2031,2155

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

SelfScriptName=$(basename "$0")
echo "---INFO: ${SelfScriptName} BEGIN $(date +%s) / $(date  +'%F %T %Z')"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
if ! source "${SCRIPT_DIR}/env.sh"; then
    echo "###-ERROR: Can't load env.sh"
    exit 1
fi
source "${SCRIPT_DIR}/functions.shinc"

#=================================================
echo
echo "${0##*/} Time Now: $(date  +'%F %T %Z')"
echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo

#=================================================
# Check node sync status if not forced to use dapp
if ! $FORCE_USE_DAPP;then
    NODE_SYNC_STATUS=$(Get_TimeDiff)
    # Get time difference between the current machine and the node
    # shellcheck disable=SC2046
    read -r MC_TIME_DIFF SH_TIME_DIFF <<< $(echo "$NODE_SYNC_STATUS" | awk '{print $1, $2}')
    # echo "---INFO: Node sync status: $MC_TIME_DIFF $SH_TIME_DIFF"
    if ! [[ $MC_TIME_DIFF =~ ^[0-9]+$ && $SH_TIME_DIFF =~ ^[0-9]+$ ]] || [[ $MC_TIME_DIFF -gt 20 ]] || [[ $SH_TIME_DIFF -gt 20 ]];then
        IFS= read -rp "### Access method: console, but your node is not synced!!! Do you want to use DAPP temporarily? (yes/no)? " </dev/tty answer
        case ${answer:0:1} in
            y|Y|yes|Yes|YES )
            export FORCE_USE_DAPP=true
                echo "Processing....." ;;
            * )
                echo "Cancelled."; exit 1 ;;
        esac
    fi
fi

#=================================================
# Check if the account is specified
ACCOUNT=$1
if [[ -z $ACCOUNT ]];then
    MY_ACCOUNT="$(cat "${KEYS_DIR}/${VALIDATOR_NAME}.addr")"
    if [[ -z $MY_ACCOUNT ]];then
        echo " Can't find ${KEYS_DIR}/${VALIDATOR_NAME}.addr"
        exit 1
    else
        ACCOUNT=$MY_ACCOUNT
    fi
else
    acc_fmt="$(echo "$ACCOUNT" |  awk -F ':' '{print $2}')"
    [[ -z $acc_fmt ]] && ACCOUNT="$(cat "${KEYS_DIR}/${ACCOUNT}.addr")"
fi
echo "Account: $ACCOUNT"
acc_wc=${ACCOUNT%%:*}
NODE_WC="0"
if [[ "${NODE_WC}" != "${acc_wc}" ]] && [[ "${acc_wc}" != "-1" ]];then
    echo -e "${BoldText}WARNING: You are ask account info from a other workchain than the node is. Result may be wrong!${NormText}"
fi
ACCOUNT_INFO="$(Get_Account_Info "$ACCOUNT")"
ACC_STATUS="$(echo "$ACCOUNT_INFO" |awk '{print $1}')"
if [[ "$ACC_STATUS" == "None" ]];then
    echo -e "${BoldText}${RedBack}Account does not exist! (no tokens, no code, nothing)${NormText}"
    echo "=================================================================================================="
    exit 0
fi
[[ "$ACC_STATUS" == "Uninit" ]] && ACC_STATUS="${BoldText}Uninit${NormText}" || ACC_STATUS="${BoldText}${GreenBack}Deployed and Active${NormText}"

AMOUNT="$(echo "$ACCOUNT_INFO" |awk '{print $2}')"
ACC_LAST_OP_TIME=$(echo "$ACCOUNT_INFO" | gawk '{ print strftime("%Y-%m-%d %H:%M:%S", $3)}')

#=================================================
# Get Elector address, Elector type, and elector boc file in ${ELECTIONS_WORK_DIR}/${elector_addr##*:}.boc
if result=$(Get_Current_Elector_Type);then
    read -r elector_addr ELECTOR_TYPE <<< "$result"
else
    echoerr "###-ERROR(${FUNCNAME[0]} line $LINENO): Cannot get Elector type!"
    echo "$result"
    exit 1
fi

#=================================================
# Check unsigned transactions in the queue
Trans_List="$(Get_MSIG_Trans_List "${ACCOUNT}")"
declare -i Trans_QTY
Trans_QTY=$(echo "${Trans_List}" | jq -r ".transactions|length")

if [[ ${Trans_QTY} -gt 0 ]];then
    declare -i Exist_El_Trans_Qty=$(echo "${Trans_List}" | jq -r "[.transactions[]|select(.dest == \"${elector_addr}\")]|length")
    if [[ "$STAKE_MODE" == "depool" ]];then
        Depool_addr=$(cat "${KEYS_DIR}/depool.addr")
        dp_proxy0=$(cat "${KEYS_DIR}/proxy0.addr")
        dp_proxy1=$(cat "${KEYS_DIR}/proxy1.addr")
        declare -i Exist_DP_Trans_Qty=$(echo "${Trans_List}" | jq -r "[.transactions[]|select(.dest == \"${Depool_addr}\")]|length")
        declare -i Exist_Proxy0_Trans_Qty=$(echo "${Trans_List}" | jq -r "[.transactions[]|select(.dest == \"${dp_proxy0}\")]|length")
        declare -i Exist_Proxy1_Trans_Qty=$(echo "${Trans_List}" | jq -r "[.transactions[]|select(.dest == \"${dp_proxy1}\")]|length")
    fi
fi
echo "Total pending transactions qty:      ${Trans_QTY}"
[[ $Exist_DP_Trans_Qty -ne 0 ]] && echo "To DePool transactions qty:  ${Exist_DP_Trans_Qty}"
[[ $Exist_El_Trans_Qty -ne 0 ]] && echo "To Elector transactions qty: ${Exist_El_Trans_Qty}"
[[ $Exist_Proxy0_Trans_Qty -ne 0 ]] && echo "To Proxy0 transactions qty:  ${Exist_Proxy0_Trans_Qty}"
[[ $Exist_Proxy1_Trans_Qty -ne 0 ]] && echo "To Proxy1 transactions qty:  ${Exist_Proxy1_Trans_Qty}"
echo

echo -e "Status: $ACC_STATUS"
echo "Has balance : $(echo "scale=3; $((AMOUNT)) / 1000000000" | $CALL_BC) tokens"
echo "Last operation time: $ACC_LAST_OP_TIME"
if [[ "$(echo "$ACCOUNT_INFO" |awk '{print $1}')" == "Active" ]];then
    Custodians="$(Get_Account_Custodians_Info "$ACCOUNT")"
    echo "Total custodians: $(echo "$Custodians"|awk '{print $1}'); Required to confirm: $(echo "$Custodians"|awk '{print $2}')"
fi

echo "=================================================================================================="
exit 0
