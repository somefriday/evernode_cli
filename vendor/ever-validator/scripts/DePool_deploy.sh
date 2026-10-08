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
echo "#################################### DePool deploy script ########################################"
SelfScriptName=$(basename "$0") && export SelfScriptName
echo "INFO: $SelfScriptName BEGIN $(date +%s) / $(date +'%F %T %Z')"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
if ! source "${SCRIPT_DIR}/env.sh"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't load env.sh"
    exit 1
fi
# shellcheck source=functions.shinc
source "${SCRIPT_DIR}/functions.shinc"
echo
echo "Current Time: $(date +'%F %T %Z')"
echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo
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
source "${SCRIPT_DIR}/functions.shinc"

echo
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
SEND_ATTEMPTS=3

#========= Depool Deploy Parametrs ================================
echo -e "\n================= Deploy DePool contract =========================="

MinStake=$(echo "${MinStakeT} * 1000000000" | $CALL_BC|cut -d '.' -f 1)
ValidatorAssurance=$(echo "${ValidatorAssuranceT} * 1000000000" | $CALL_BC|cut -d '.' -f 1)

ProxyCode="$($CALL_CLI -j decode stateinit --tvc "${INPL_DSCs_DIR}/DePoolProxy.tvc" | jq -r '.code')"
[[ -z $ProxyCode ]] && echo "###-ERROR(${SelfScriptName} line $LINENO): DePoolProxy.tvc not found in ${DSCs_DIR} dir" && exit 1

DepoolCode="$($CALL_CLI -j decode stateinit --tvc "${INPL_DSCs_DIR}/DePool.tvc" | jq -r '.code')"
[[ -z $DepoolCode ]] && echo "###-ERROR(${SelfScriptName} line $LINENO): DePool.tvc not found in ${DSCs_DIR} dir" && exit 1

Validator_addr="$(cat "${KEYS_DIR}/${VALIDATOR_NAME}.addr")"
[[ -z $Validator_addr ]] && echo "###-ERROR(${SelfScriptName} line $LINENO): Validator address not found in ${KEYS_DIR}/${VALIDATOR_NAME}.addr" && exit 1

Validator_WC=${Validator_addr%%:*}
if [[ $Validator_WC -eq -1 ]];then
    echo "###-WARNING(${SelfScriptName} line $LINENO): Validator address is in masterchain(-1). It is not recommended to use it for DePool. Check it twice!"
fi

#=================================================
# Addresses and vars
Depool_Name=$1
Depool_Name=${Depool_Name:="depool"}
WAL_NAME=$Depool_Name
[[ "$Depool_Name" == "depool" ]] && WAL_NAME="$VALIDATOR_NAME"
Depool_addr="$(cat "${KEYS_DIR}/${Depool_Name}.addr")"
[[ -z $Depool_addr ]] && echo "###-ERROR(${SelfScriptName} line $LINENO): Depool address not found in ${KEYS_DIR}/${Depool_Name}.addr" && exit 1
Depool_WC=${Depool_addr%%:*}
if [[ $Depool_WC -eq -1 ]];then
    echo "###-WARNING(${SelfScriptName} line $LINENO): Depool address is in masterchain(-1). It is not recommended to use it for DePool. Check it twice!"
fi
if [[ $Depool_WC -ne $Validator_WC ]] && [[ $Validator_WC -ne -1 ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Depool_WC=${Depool_WC} not equal Validator_WC=${Validator_WC}"
    exit 1
fi
Depool_Public_Key="$(jq -r ".public" "${KEYS_DIR}/${Depool_Name}.keys.json")"
[[ -z $Depool_Public_Key ]] && echo "###-ERROR(${SelfScriptName} line $LINENO): Depool_Public_Key not found in ${KEYS_DIR}/${Depool_Name}.keys.json" && exit 1

if [[ ${Validator_WC} -ne ${Depool_WC} ]] && [[ ${Validator_WC} -ne -1 ]] && [[ ${Depool_WC} -ne 0 ]];then
    echo -e "${BoldText}${YellowBack}###-WARNING(${SelfScriptName} line $LINENO): Depool_WC=${Depool_WC} not equal Validator_WC=${Validator_WC}!! ${NormText}" 
    # exit 1
fi

#===========================================================
# Check DePool Address from Keys
if ! DP_ADDR_from_Keys="$($CALL_CLI genaddr ${INPL_DSCs_DIR}/DePool.tvc \
            --abi ${INPL_DSCs_DIR}/DePool.abi.json \
            --setkey "${INPL_KEYS_DIR}/${Depool_Name}.keys.json" \
            --wc "$Depool_WC")"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't generate address from keys for DePool contract"
    echo "$DP_ADDR_from_Keys"
    echo -e "----------------------------------------------------------------------------------------------------------------------------\n"
    exit 1
fi
DP_ADDR_from_Keys="$(echo "$DP_ADDR_from_Keys" | grep "Raw address:" | awk '{print $3}')"
if [[ ! "$Depool_addr" == "$DP_ADDR_from_Keys" ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Given DePool Address and calculated address is different. Possible you prepared it for another contract. "
    echo "Given addr: $Depool_addr"
    echo "Calc  addr: $DP_ADDR_from_Keys"
    echo 
    exit 1
fi

#===========================================================
# check depool balance
Depool_INFO="$(Get_Account_Info "${Depool_addr}")"
Depool_Status=$(echo "$Depool_INFO" | awk '{print $1}')
Depool_AMOUNT=$(echo "$Depool_INFO" | awk '{print $2}')

if [[ "$Depool_Status" == "None" ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): DePool address '${Depool_addr}' not found on the blockchain with any tokens!"
    exit 1
fi

if [[ $Depool_AMOUNT -lt $((BalanceThresholdT * 1000000000 * 2  + 5000000000)) ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): You have not anought balance on depool address!"
    echo -e "It should have at least $((BalanceThresholdT * 2  + 5)), but now it has $((Depool_AMOUNT / 1000000000))\n"
    exit 1
fi

if [[ "$Depool_Status" != "Uninit" ]];then
    echo -e "###-ERROR(${SelfScriptName} line $LINENO): Depool_Status not 'Uninit'. Already deployed?\n"
    echo "Depool balance: $((Depool_AMOUNT/1000000000)) ; status: $Depool_Status"
    exit 1
fi

echo "Depool balance: $((Depool_AMOUNT/1000000000)) ; status: $Depool_Status"
echo -e "${BoldText}REMEMBER: Depool CRITICAL_THRESHOLD is 10 tokens. If the depool balance is less than 10 tokens, the depool will be stuck and will not be able to operate at all.${NormText}"
echo
#===========================================================
# print INFO
echo "Validator Name:    $VALIDATOR_NAME"
echo "Validator_addr:    $Validator_addr"
echo "Depool Address:    $Depool_addr"
echo "     Depool WC:    $Depool_WC"
echo "Depool_Public_Key: $Depool_Public_Key"
echo
echo "Minimal Stake:                $MinStakeT"
echo "ParticipantRewardFraction:    $ParticipantRewardFraction"
echo "ValidatorAssurance:           $ValidatorAssuranceT"
echo
echo "First 64 syms from DePoolCode:  ${DepoolCode:0:64}"
echo "First 64 syms from ProxyCode:   ${ProxyCode:0:64}"

echo -e "\nPayload for DePool deploy message:"
Payload_for_Deploy="{\"minStake\":$MinStake,\
\"validatorAssurance\":$ValidatorAssurance,\
\"proxyCode\":\"$ProxyCode\",\
\"validatorWallet\":\"$Validator_addr\",\
\"participantRewardFraction\":$ParticipantRewardFraction}"
if ! echo "$Payload_for_Deploy"|jq; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Error in JSON format for DePool deploy message"
    exit 1
fi

#===========================================================
read -rp "### CHECK INFO TWICE!!! Is this a right Parameters? Think once more!  (yes/n)? " </dev/tty answer
case ${answer:0:3} in
    yes|YES )
        echo
        echo "Processing....."
    ;;
    * )
        echo
        echo "If you absolutely sure, type 'yes' "
        echo "Cancelled."
        exit 1
    ;;
esac

###################################################################################################################################
# Deploy wallet

#=================================================
# make boc file
echo -e "\n---INFO(${SelfScriptName} line $LINENO): Make deploy message BOC file..."
rm -f "${KEYS_DIR}/${WAL_NAME}_depool_deploy.boc"|cat &> /dev/null
if ! CLI_OUTPUT="$($CALL_CLI deploy_message \
    ${INPL_DSCs_DIR}/DePool.tvc \
    "${Payload_for_Deploy}" \
    --abi ${INPL_DSCs_DIR}/DePool.abi.json \
    --sign ${INPL_KEYS_DIR}/${Depool_Name}.keys.json ${SIG_ID_OPTION} \
    --wc ${Depool_WC} \
    --raw \
    --output "${INPL_KEYS_DIR}/${WAL_NAME}_depool_deploy.boc")"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot make deploy message BOC file"
    echo "$CLI_OUTPUT" | tee -a "${KEYS_DIR}/${WAL_NAME}_depool_deploy_msg.log"
    echo -e "----------------------------------------------------------------------------------------------------------------------------\n"
    exit 1
fi
echo "$CLI_OUTPUT" | tee -a "${KEYS_DIR}/${WAL_NAME}_depool_deploy_msg.log"

if [[ ! -f "${KEYS_DIR}/${WAL_NAME}_depool_deploy.boc" ]];then 
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot find deploy message BOC file ${KEYS_DIR}/${WAL_NAME}_depool_deploy.boc"
    echo "$CLI_OUTPUT"
    exit 1
fi

MBF_addr="$(echo "$CLI_OUTPUT"|grep "Contract's address:"|awk '{print $3}')"
if [[ "${MBF_addr}" != "${Depool_addr}" ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Address from BOC ($MBF_addr) is not equal calc address (${Depool_addr}) !"
    exit 1
else
    echo "---INFO: BOC file for deploy depool created: ${KEYS_DIR}/${WAL_NAME}_depool_deploy.boc"
fi

#=================================================
# Send deploy message to BlockChain
echo -e "\n---INFO(${SelfScriptName} line $LINENO): Send deploy message to blockchain..."
Attempts_to_send=$SEND_ATTEMPTS
while [[ $Attempts_to_send -gt 0 ]]; do
    if ! result="$(Send_File_To_BC "${INPL_KEYS_DIR}/${WAL_NAME}_depool_deploy.boc")"; then
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
echo "Deploy message log saved to ${KEYS_DIR}/${Depool_Name}_deploy_depool_msg.log"
echo
echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "================================================================================================"
echo

exit 0
