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

echo
echo "################################## Deploy wallet script ########################################"
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
# shellcheck source=functions.shinc
source "${SCRIPT_DIR}/functions.shinc"

echo
echo -e "$(DispEnvInfo)"
echo

SEND_ATTEMPTS=3

function show_usage(){
echo
echo " Use: $SelfScriptName <Wallet name> <'Safe' or 'SetCode'> <Num of custodians> <Min Num of signatures>"
echo " All fields required!"
echo " <Wallet Name> - name of wallet. use \$VALIDATOR_NAME for validator wallet or '--' for auto name"
echo " All files for deploy will search in '$KEYS_DIR'."
echo " If the wallet name is equal VALIDATOR_NAME than keys files '\$VALIDATOR_NAME_[1..31].keys.json' will be used"
echo "   first signature file is used to sign deploy message"
echo " <Min Num of signatures> must be less or equal of <Num of custodians>"
echo " Be very careful to:"
echo " Use 'force' as last argument to try to deploy wallet even if it deployed already or addr from keys is not equal to calculated"
echo
echo " Example: ./$SelfScriptName -- Safe 3 2"
echo
exit 0
}
[[ $# -lt 3 ]] && show_usage

#=================================================
# If the access mode is set to console and the node is not synchronized, prompt the user to switch to dapp mode temporarily
if Ask_to_switch_to_dapp;then
    echo "Proceeding..."
    export FORCE_USE_DAPP=true
elif [[ $? -gt 1 ]];then
    echo "Cancelled."
    exit 1
fi

#============================================
echo "Deploy wallet to '${NETWORK_TYPE}' network"

#==================================================
# Check input parametrs
WAL_NAME=$1
if [[ "$WAL_NAME" == "--" ]];then WAL_NAME=$VALIDATOR_NAME; fi
if [[ "$WAL_NAME" != "$VALIDATOR_NAME" ]];then
    echo "###-WARNING(${SelfScriptName} line $LINENO): Wallet name is not equal to VALIDATOR_NAME ($VALIDATOR_NAME) in env.sh"
fi

CodeOfWallet="$2"
if [[ ! $CodeOfWallet == "Safe" ]] && [[ ! $CodeOfWallet == "SetCode" ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Wrong code of wallet. Choose 'Safe' or 'SetCode'"
    show_usage
    exit 1
fi
Cust_QTY=$3
if [[ $Cust_QTY -lt 1 ]] || [[ $Cust_QTY -gt 32 ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Wrong Num of custodians must be >= 1 and <= 31"  
    show_usage
    exit 1
fi
ReqConfirms=$4
if [[ $ReqConfirms -gt $Cust_QTY ]] || [[ $ReqConfirms -lt 1 ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Wrong Required num of signatures."
    show_usage
    exit 1
fi
ForceDeploy=$5

#==================================================
# Get Wallet address for deploy
echo "Wallet Name: $WAL_NAME"
WALL_ADDR="$(cat "${KEYS_DIR}/${WAL_NAME}.addr")"
if [[ -z $WALL_ADDR ]];then
    echo -e "\n###-ERROR(${SelfScriptName} line $LINENO): Cannot get wallet address from file  ${KEYS_DIR}/${WAL_NAME}.addr\n"
    exit 1
fi
echo "Wallet addr for deploy : $WALL_ADDR"

#==================================================
# Get Wallet work chain for deploy
Work_Chain=$(echo "${WALL_ADDR}" | cut -d ':' -f 1)
echo "Wallet work chain for deploy : $Work_Chain"

#=================================================
# Check deployed already
ACCOUNT_INFO="$(Get_Account_Info "${WALL_ADDR}")"
AMOUNT=$(echo "$ACCOUNT_INFO" |awk '{print $2}')
ACTUAL_BALANCE="$(echo "scale=3; $((AMOUNT)) / 1000000000" | $CALL_BC)"
ACC_STATUS=$(echo "$ACCOUNT_INFO" | awk '{print $1}')
if [[ "$ACC_STATUS" == "Active" ]];then
    echo -e "\n###-ERROR(${SelfScriptName} line $LINENO): ${YellowBack}${BoldText}Wallet deployed already.${NormText} Status: \"$ACC_STATUS\"; Balance: $ACTUAL_BALANCE\n"
    [[ "${ForceDeploy}" != "force" ]] && exit 1
fi
echo "Wallet status : \"$ACC_STATUS\""

#=================================================
# Check wallet balance
if [[ $((AMOUNT / 100000000)) -lt 9 ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): You haven't enough tokens to deploy wallet. Current balance: $ACTUAL_BALANCE tokens. You need 0.9 at least. Exit."
    exit 1
fi
echo "Wallet balance: $ACTUAL_BALANCE"

#================================================================
# Set Wallet Code and ABI
Wallet_Code=${INPL_SafeSCs_DIR}/SafeMultisigWallet.tvc
Wallet_ABI=${INPL_SafeSCs_DIR}/SafeMultisigWallet.abi.json
if [[ "$CodeOfWallet" == "SetCode" ]];then
    Wallet_Code=${INPL_SetSCs_DIR}/SetcodeMultisigWallet.tvc
    Wallet_ABI=${INPL_SetSCs_DIR}/SetcodeMultisigWallet.abi.json
fi

echo "Wallet Code: $Wallet_Code"
echo "ABI for wallet: $Wallet_ABI"
#=================================================
# Read all pubkeys and make a string
Custodians_PubKeys=""
for (( i=1; i <= Cust_QTY; i++))
do
    PubKey="0x$(jq '.public' "${KEYS_DIR}/${WAL_NAME}_${i}.keys.json" | tr -d '\"')"
    SecKey="0x$(jq '.secret' "${KEYS_DIR}/${WAL_NAME}_${i}.keys.json" | tr -d '\"')"
    if [[ "$PubKey" == "0x" ]] || [[ "$SecKey" == "0x" ]];then
        echo
        echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find wallet public and/or secret key from file ${KEYS_DIR}/${WAL_NAME}_${i}.keys.json"
        echo
        exit 1
    fi

    Custodians_PubKeys+="\"${PubKey}\","
done

Custodians_PubKeys=${Custodians_PubKeys::-1}
echo "Custodians_PubKeys: '$Custodians_PubKeys'"
echo "Custodians QTY: $Cust_QTY; Required signs: $ReqConfirms"
echo

#===========================================================
# Check Wallet Address
if ! ADDR_from_Keys="$($CALL_CLI genaddr $Wallet_Code --abi $Wallet_ABI --setkey "${INPL_KEYS_DIR}/${WAL_NAME}_1.keys.json" --wc "$Work_Chain")";then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot generate address from keys file ${INPL_KEYS_DIR}/${WAL_NAME}_1.keys.json"
    exit 1
fi
ADDR_from_Keys=$(echo "${ADDR_from_Keys}" | grep "Raw address:"  | awk '{print $3}')
if [[ ! "$WALL_ADDR" == "$ADDR_from_Keys" ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Given Wallet Address and calculated address is different. Possible you prepared it for another contract type or keys. "
    echo "Given addr: $WALL_ADDR"
    echo "Calc  addr: $ADDR_from_Keys"
    echo 
    [[ "${ForceDeploy}" != "force" ]] && exit 1
fi
###################################################################################################################################
# Deploy wallet

#=================================================
# make boc file 
echo -e "\n---INFO(${SelfScriptName} line $LINENO): Make deploy message BOC file..."
rm -f "${KEYS_DIR}/${WAL_NAME}_msig_deploy.boc"|cat &> /dev/null
if ! CLI_OUTPUT="$($CALL_CLI deploy_message \
    "$Wallet_Code" \
    "{\"owners\":[$Custodians_PubKeys],\"reqConfirms\":${ReqConfirms}}" \
    --abi $Wallet_ABI \
    --sign "${INPL_KEYS_DIR}/${WAL_NAME}_1.keys.json" ${SIG_ID_OPTION} \
    --wc $Work_Chain \
    --raw \
    --output "${INPL_KEYS_DIR}/${WAL_NAME}_msig_deploy.boc" 2>&1)"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot make deploy message BOC file"
    echo "$CLI_OUTPUT" | tee -a "${KEYS_DIR}/${WAL_NAME}_msig_deploy_msg.log"
    echo -e "----------------------------------------------------------------------------------------------------------------------------\n"
    exit 1
fi
echo "$CLI_OUTPUT" | tee -a "${KEYS_DIR}/${WAL_NAME}_msig_deploy_msg.log"

if [[ ! -f "${KEYS_DIR}/${WAL_NAME}_msig_deploy.boc" ]];then 
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot find deploy message BOC file ${KEYS_DIR}/${WAL_NAME}_msig_deploy.boc"
    echo "$CLI_OUTPUT"
    exit 1
fi

MBF_addr="$(echo "$CLI_OUTPUT"|grep "Contract's address:"|awk '{print $3}')"

if [[ "${MBF_addr}" != "${WALL_ADDR}" ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Address from BOC ($MBF_addr) is not equal calc address ($WALL_ADDR) !"
    [[ "${ForceDeploy}" != "force" ]] && exit 1
else
    echo "---INFO: BOC file for deploy wallet created: ${KEYS_DIR}/${WAL_NAME}_msig_deploy.boc"
fi

#=================================================
# Send deploy message to BlockChain
echo -e "\n---INFO(${SelfScriptName} line $LINENO): Send deploy message to blockchain..."
Attempts_to_send=$SEND_ATTEMPTS
while [[ $Attempts_to_send -gt 0 ]]; do
    if ! result="$(Send_File_To_BC "${INPL_KEYS_DIR}/${WAL_NAME}_msig_deploy.boc")"; then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Send deploy message FAILED!!!"
        echo "$result"
        ((Attempts_to_send--))
        echo "###-WARNING(${SelfScriptName} line $LINENO): Retry send deploy message to blockchain. Attempts left: $Attempts_to_send"
    else
        echo "DONE"
        break
    fi
done
if [[ $Attempts_to_send -eq 0 ]];then
    echo -e "###-ERROR(${SelfScriptName} line $LINENO): Cannot send deploy message to blockchain. Exit.\n"
    exit 1
fi

echo
echo "Deploy message log saved to ${KEYS_DIR}/${WAL_NAME}_deploy_wallet_msg.log"
echo
echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "================================================================================================"
echo

exit 0
