#!/usr/bin/env bash
# shellcheck disable=SC2031
set -eE

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
 
echo
echo "#################################### Send tokens script ########################################"
SelfScriptName=$(basename "$0")
echo "INFO: $SelfScriptName BEGIN $(date +%s) / $(date  +'%F %T %Z')"

function tr_usage(){
    echo
    echo " use: transfer_amount.sh <SRC> <DST> <AMOUNT> [new]"
    echo " where:"
    echo "   <SRC> - source account name or address. If '--' given, then use the current validator account."
    echo "   <DST> - destination account name or address."
    echo "   <AMOUNT> - amount of tokens to transfer."
    echo "   [new] - optional parameter for transfer to not activated account (for creation)"
    echo
    exit 0
}

[[ $# -le 2 ]] && tr_usage

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/functions.shinc"
echo
echo "Time Now: $(date  +'%F %T %Z')"
echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo

#=================================================
# If the access mode is set to console and the node is not synchronized, prompt the user to switch to dapp mode temporarily
if Ask_to_switch_to_dapp;then
    echo "Proceeding..."
    export FORCE_USE_DAPP=true
elif [[ $? -gt 1 ]];then
    echo "Cancelled."
    exit 1
fi

SEND_ATTEMPTS="10"

#===========================================================
# Check wallet code & ABI
Wallet_Code=${SafeSCs_DIR}/SafeMultisigWallet.tvc
Wallet_ABI=${SafeSCs_DIR}/SafeMultisigWallet.abi.json
if [[ ! -f $Wallet_Code ]] || [[ ! -f $Wallet_ABI ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can not find Wallet code or ABI. Check contracts folder."  
    show_usage
    exit 1
fi
echo "Wallet Code: $Wallet_Code"
echo "ABI for wallet: $Wallet_ABI"

#===========================================================
# 
SRC_NAME=$1
[[ "$SRC_NAME" == "--" ]] && SRC_NAME=$VALIDATOR_NAME
DST_NAME=$2
TRANSF_AMOUNT="$3"
declare -i NANO_AMOUNT
NEW_ACC=$4
[[ -z $TRANSF_AMOUNT ]] && tr_usage

NANO_AMOUNT=$(echo "$TRANSF_AMOUNT * 1000000000" | $CALL_BC|cut -d '.' -f 1)
if [[ $NANO_AMOUNT -lt 100000 ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't transfer too small amount of nanotokens! (${NANO_AMOUNT})nt"
    exit 1
fi
echo "Nanotokens to transfer: $NANO_AMOUNT"

if [[ "$NEW_ACC" == "new" ]];then
    BOUNCE="false"
else
    BOUNCE="true"
fi

# SRC account should be file name in the keys directory and has aproppriate keys file in it
if ! SRC_ACCOUNT="$(cat "${KEYS_DIR}/${SRC_NAME}.addr")";then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Given SRC account name not found in the keys directory (${KEYS_DIR}/${SRC_NAME}.addr)"
    exit 1
fi
SRC_WC=$(echo "$SRC_ACCOUNT" | cut -d ':' -f 1)

# DST account should be file name in the keys directory or address in the format "wc:addr"
DST_ACCOUNT=$DST_NAME
dst_acc_fmt="$(echo "$DST_ACCOUNT" |  awk -F ':' '{print $2}')"
# check if DST account is given in format "wc:addr" and addr is 64 hex symbols
if [[ -z $dst_acc_fmt ]] || [[ ! ${#dst_acc_fmt} -eq 64 ]] || [[ ! $dst_acc_fmt =~ ^[0-9A-Fa-f]+$ ]];then
    # assume that DST account is given as name and try to find it in the keys directory
    if ! DST_ACCOUNT="$(cat "${KEYS_DIR}/${DST_NAME}.addr")";then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Given DST account is not in the format 'wc:addr' and not found in the keys directory"
        exit 1
    fi
fi
# DST_WC=$(echo "$DST_ACCOUNT" | cut -d ':' -f 1)

msig_public="$(jq -r '.public' "${KEYS_DIR}/${SRC_NAME}_1.keys.json")"
msig_secret="$(jq -r '.secret' "${KEYS_DIR}/${SRC_NAME}_1.keys.json")"
if [[ -z $msig_public ]] || [[ -z $msig_secret ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find SRC keypair file (${KEYS_DIR}/${SRC_NAME}_1.keys.json) in the keys directory"
    exit 1
fi

#================================================================
echo "Check SRC $SRC_NAME account.."
if ! ACCOUNT_INFO="$(Get_Account_Info "$SRC_ACCOUNT")";then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't get account info for $SRC_ACCOUNT account"
    exit 1
fi
SRC_STATUS="$(echo "$ACCOUNT_INFO" |awk '{print $1}')"
# shellcheck disable=SC2155
declare -i SRC_AMOUNT=$(echo "$ACCOUNT_INFO" |awk '{print $2}')
SRC_TIME="$(echo "$ACCOUNT_INFO" | gawk '{ print strftime("%Y-%m-%d %H:%M:%S", $3)}')"
SRC_Time_Unix=$(echo "$ACCOUNT_INFO" |awk '{print $3}')

if [[ "$SRC_STATUS" == "None" ]];then
    echo -e "###-ERROR(${SelfScriptName} line $LINENO): ${BoldText}${RedBack}SRC account does not exist! (no tokens, no code, nothing)${NormText}"
    echo "=================================================================================================="
    echo 
    exit 0
fi
if [[ "$SRC_STATUS" == "Uninit" ]];then
    echo -e "###-ERROR(${SelfScriptName} line $LINENO): ${BoldText}${RedBack}SRC account uninitialized!${NormText} Deploy contract code first!"
    echo "=================================================================================================="
    echo 
    exit 0
fi

#================================================================
# Check SRC acc Keys
Calc_Addr="$($CALL_CLI genaddr "${INPL_SafeSCs_DIR}/SafeMultisigWallet.tvc" \
    --abi "${INPL_SafeSCs_DIR}/SafeMultisigWallet.abi.json" \
    --setkey "${INPL_KEYS_DIR}/${SRC_NAME}_1.keys.json" \
    --wc "$SRC_WC" | grep "Raw address:" | awk '{print $3}')"

if [[ ! "$SRC_ACCOUNT" == "$Calc_Addr" ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Given SRC account address and calculated address is different. Wrong keys."
    echo "Given addr: $SRC_ACCOUNT"
    echo "Calc  addr: $Calc_Addr"
    echo 
    if [[ "$NEW_ACC" == "force" ]] || [[ "$5" == "force" ]];then
        echo "###-ATTENTION!! 'force' option is given! Script will do ONE attempt to send tokens!"
        SEND_ATTEMPTS=1
    else
        exit 1
    fi
fi

Custodians="$(Get_Account_Custodians_Info "$SRC_ACCOUNT")"
SRC_Conf_QTY=$(echo "$Custodians"|awk '{print $2}')

#================================================================
# Check DST account
echo "Check DST $DST_NAME account.."
ACCOUNT_INFO="$(Get_Account_Info "$DST_ACCOUNT")"
# shellcheck disable=SC2155
declare -i DST_AMOUNT=`echo "$ACCOUNT_INFO" |awk '{print $2}'`
DST_TIME="$(echo "$ACCOUNT_INFO" | gawk '{ print strftime("%Y-%m-%d %H:%M:%S", $3)}')"
DST_STATUS=$(echo "$ACCOUNT_INFO" |awk '{print $1}')
if [[ ! "$DST_STATUS" == "Active" ]] && [[ -z $NEW_ACC ]];then
    echo
    echo "###-ERROR(${SelfScriptName} line $LINENO): DST account not deployed. Use 'new' parameter to transfer to undeployed account."
    tr_usage
    exit 1
fi

#================================================================
Trans_List="$(Get_MSIG_Trans_List "${SRC_ACCOUNT}")"
# shellcheck disable=SC2155
declare -i Before_Trans_QTY=$(echo "$Trans_List" | jq -r ".transactions|length")

[[ $Before_Trans_QTY -ne 0 ]] && echo "+++WARNING(${SelfScriptName} line $LINENO): You have $Before_Trans_QTY unsigned transactions already."
echo
echo "TRANFER FROM ${SRC_NAME} :"
echo "SRC Account: $SRC_ACCOUNT"
echo "Has balance : $(echo "scale=3; $((SRC_AMOUNT)) / 1000000000" | $CALL_BC) tokens"
echo "Last operation time: $SRC_TIME"
echo
echo "TRANFER TO ${DST_NAME} :"
echo "DST Account: $DST_ACCOUNT"
echo "DST Account status: $DST_STATUS"
echo "Has balance : $(echo "scale=3; $((DST_AMOUNT)) / 1000000000" | $CALL_BC) tokens"
echo "Last operation time: $DST_TIME"
echo
echo "Transferring $TRANSF_AMOUNT ($NANO_AMOUNT) from ${SRC_NAME} to ${DST_NAME} ..." 

if [[ $SRC_AMOUNT -le $NANO_AMOUNT ]];then
    echo
    echo "###-ERROR(${SelfScriptName} line $LINENO): You cannot transfer more than you have. Sorry.."
    echo
    exit 1
fi

read -rp "### CHECK INFO TWICE!!! Is this a right tranfer?  (y/n)? " </dev/tty answer
case ${answer} in
    y|Y|yes|Yes|YES )
        echo "Processing..."
    ;;
    * )
        echo "Cancelled."
        exit 1
    ;;
esac

#================================================================
# Make BOC file to send
echo "INFO: Making BOC file to send..."
TA_BOC_File="${KEYS_DIR}/Transfer_Amount.boc"
rm -f "${TA_BOC_File}" &>/dev/null
if ! TC_OUT="$($CALL_CLI message --raw --output "${INPL_KEYS_DIR}/Transfer_Amount.boc" \
--sign "${INPL_KEYS_DIR}/${SRC_NAME}_1.keys.json" ${SIG_ID_OPTION} \
--abi "${INPL_SafeC_Wallet_ABI}" \
"${SRC_ACCOUNT}" submitTransaction \
"{\"dest\":\"${DST_ACCOUNT}\",\"value\":${NANO_AMOUNT},\"bounce\":$BOUNCE,\"allBalance\":false,\"payload\":\"\"}" \
--lifetime 600)";then
    echo
    echo "###-ERROR(${SelfScriptName} line $LINENO): Failed to make BOC file ${TA_BOC_File}. Can't continue."
    echo "$TC_OUT"
    echo "=================================================================================================="
    exit 1
fi

echo -e "\n-----------------------------------------------------------"
echo "$TC_OUT"
echo -e "-----------------------------------------------------------\n"

TC_OUTPUT="$(echo "$TC_OUT" | grep -i 'Message saved to file')"

if [[ -z $TC_OUTPUT ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Failed to make BOC file ${TA_BOC_File}. Can't continue."
    exit 1
fi
echo "INFO: Message BOC file created: ${TA_BOC_File}"

# ==========================================================================
for (( i=1; i <= SEND_ATTEMPTS; i++ )); do
    echo -n "INFO: submitTransaction attempt #${i}..."
    result="$(Send_File_To_BC "${INPL_KEYS_DIR}/Transfer_Amount.boc")"
    if [[ "$result" == "failed" ]]; then
        echo " FAIL"
        echo "Now sleep $LC_Send_MSG_Timeout secs and will try again.."
        echo "--------------"
        sleep $LC_Send_MSG_Timeout
        continue
    else
        echo " PASS"
    fi
    
    echo "Now sleep $LC_Send_MSG_Timeout secs and check transactions..."
    sleep $LC_Send_MSG_Timeout

   if [[ $SRC_Conf_QTY -le 1 ]];then
        ACCOUNT_INFO="$(Get_Account_Info "$SRC_ACCOUNT")"
        Time_Unix=$(echo "$ACCOUNT_INFO" |awk '{print $3}')
        if [[ $Time_Unix -gt $SRC_Time_Unix ]];then
            echo -e "INFO: successfully sent $TRANSF_AMOUNT tokens."
            break
        fi
   fi

    Trans_List="$(Get_MSIG_Trans_List "${SRC_ACCOUNT}")"
    # shellcheck disable=SC2155
    declare -i Trans_QTY=$(echo "$Trans_List" | jq -r ".transactions|length")
    if [[ $Trans_QTY -gt $Before_Trans_QTY ]] && [[ $SRC_Conf_QTY -gt 1 ]];then
        Last_Trans_ID="$(echo "$Trans_List" | jq -r .transactions[$((Trans_QTY - 1))].id)"
        echo -e "\n${BoldText}INFO: successfully created transaction # $Last_Trans_ID"
        break
   fi
done

[[ $Trans_QTY -gt 0 ]] && echo -e "${BoldText}${RedBack}+++WARNING(${SelfScriptName} line $LINENO): You have $Trans_QTY unsigned transactions now.${NormText}\n"

# ==========================================================================
# Check balance after transfer
echo "Check SRC $SRC_NAME account.."
ACCOUNT_INFO="$(Get_Account_Info "$SRC_ACCOUNT")"
SRC_AMOUNT=$(echo "$ACCOUNT_INFO" |awk '{print $2}')
SRC_TIME=$(echo "$ACCOUNT_INFO" | gawk '{ print strftime("%Y-%m-%d %H:%M:%S", $3)}')

echo "Check DST $DST_NAME account.."
ACCOUNT_INFO="$(Get_Account_Info "$DST_ACCOUNT")"
DST_AMOUNT=$(echo "$ACCOUNT_INFO" |awk '{print $2}')
DST_TIME="$(echo "$ACCOUNT_INFO" | gawk '{ print strftime("%Y-%m-%d %H:%M:%S", $3)}')"

echo
echo "${SRC_NAME} Account: $SRC_ACCOUNT"
echo "Has balance : $(echo "scale=3; $((SRC_AMOUNT)) / 1000000000" | $CALL_BC) tokens"
echo "Last operation time: $SRC_TIME"

echo
echo "${DST_NAME} Account: $DST_ACCOUNT"
echo "Has balance : $(echo "scale=3; $((DST_AMOUNT)) / 1000000000" | $CALL_BC) tokens"
echo "Last operation time: $DST_TIME"
echo

echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date)"
echo "=================================================================================================="
exit 0
