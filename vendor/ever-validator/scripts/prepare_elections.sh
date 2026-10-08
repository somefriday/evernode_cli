#!/usr/bin/env bash
# shellcheck disable=SC2031,SC2119,SC2155
# shellcheck source=./env.sh

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

###################
declare -ir TIMEDIFF_MAX=20
declare -ir SLEEP_TIMEOUT=20
###################
readonly DePoolTik_Payload="te6ccgEBAQEABgAACCiAmCM="
readonly DePoolReplenish_Payload='te6ccgEBAQEABgAACGhEx+s='
declare -ir NANOSTAKE=$((1 * 1000000000))
declare -ir TOPUP_THRESHOLD=1920000000 # 1.92 tokens
###################

echo
echo "################################ Prepare elections script ######################################"
SelfScriptName=$(basename "$0")
echo "--- INFO: ${SelfScriptName} BEGIN $(date +%s) / $(date  +'%F %T %Z')"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
source "${SCRIPT_DIR}/env.sh"
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


if [[ $NODE_ROLE != "validator" ]];then
    echo "###-WARNING(${SelfScriptName} line $LINENO): NODE_ROLE is not 'validator' ($NODE_ROLE). Will try to return stake only."
fi

[[ ! -d "${ELECTIONS_HISTORY_DIR}" ]] && mkdir -p "${ELECTIONS_HISTORY_DIR}"

#=================================================
echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo

##############################################################################
# Check node sync
# masterchain timediff
#=================================================
# Get node sync status
NODE_SYNC_STATUS=$(Get_TimeDiff)
# Get time difference between the current machine and the node
# shellcheck disable=SC2046
read -r MC_TIME_DIFF SH_TIME_DIFF <<< $(echo "$NODE_SYNC_STATUS" | awk '{print $1, $2}')
echo "---INFO: Node sync status: $MC_TIME_DIFF $SH_TIME_DIFF"
if ! [[ $MC_TIME_DIFF =~ ^[0-9]+$ && $SH_TIME_DIFF =~ ^[0-9]+$ ]]; then
    # If one of the values is not a number, we assume that the condition for the crown is met
    MC_TIME_DIFF=888
    SH_TIME_DIFF=888
    echo "---INFO: Modified sync status: $MC_TIME_DIFF $SH_TIME_DIFF"
fi

if [[ $MC_TIME_DIFF -gt $TIMEDIFF_MAX ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Your node is not synced with MC. Wait until MC sync (<$TIMEDIFF_MAX) Current MC timediff: $MC_TIME_DIFF"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" \
        "$Tg_SOS_sign ###-ERROR(${SelfScriptName} line $LINENO): Your node is not synced. Wait until MC sync (<$TIMEDIFF_MAX) Current MC timediff: $MC_TIME_DIFF" > /dev/null 2>&1
    exit 1
fi
# echo "---INFO: Current MC TimeDiff: $MC_TIME_DIFF"

# shards timediff (by worst shard)
if [[ $SH_TIME_DIFF -gt $TIMEDIFF_MAX ]];then
    echo -e "${YellowBack}${BoldText}###-WARNING(${SelfScriptName} line $LINENO): Your node is not synced with WORKCHAIN. Wait for all shards to sync or your accounts may not be accessible (<$TIMEDIFF_MAX) Current shards (by worst shard) timediff: $SH_TIME_DIFF${NormText}"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" \
     "${Tg_SOS_sign} ###-ERROR(${SelfScriptName} line $LINENO): Your node is not synced with WORKCHAIN. Wait for all shards to sync or your accounts may not be accessible (<$TIMEDIFF_MAX) Current shards (by worst shard) timediff: $SH_TIME_DIFF" > /dev/null 2>&1
    exit 1
fi
# echo "---INFO: Current WC TimeDiff: $SH_TIME_DIFF"

#=================================================
# Get elections ID
declare -i elections_id=$(Get_Current_Elections_ID)
echo "---INFO:      Election ID: $elections_id"
if [[ $elections_id -gt 0 ]];then
    CURR_ELECTIONS_DIR="${ELECTIONS_WORK_DIR}/${elections_id}"
    INPL_CURR_ELECTIONS_DIR="${INPL_ELECTIONS_WORK_DIR}/${elections_id}"
    [[ ! -d "${CURR_ELECTIONS_DIR}" ]] && mkdir -p "${CURR_ELECTIONS_DIR}" && chmod ugo+rw "${CURR_ELECTIONS_DIR}"
fi
if [[ -f "${CURR_ELECTIONS_DIR}/${elections_id}_elector-confirmed-bid.txt" ]];then
    echo "---INFO: We have already participated in these elections: ($elections_id)"
    exit 1
fi

#=================================================
# Load addresses and set variables
Validator_addr="$(cat "${KEYS_DIR}/${VALIDATOR_NAME}.addr" 2>/dev/null|cat)"
Work_Chain=${Validator_addr%%:*}
if [[ -z $Validator_addr ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find validator address! ${KEYS_DIR}/${VALIDATOR_NAME}.addr"
    exit 1
fi
if [[ ! -f ${SafeC_Wallet_ABI} ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): ${SafeC_Wallet_ABI} NOT FOUND! Can't continue"
    exit 1
fi
Validator_Acc_Info="$(Get_Account_Info "${Validator_addr}")"
declare -i Validator_Acc_LT=$(echo "$Validator_Acc_Info" | awk '{print $3}')
Val_Adrr_HEX=${Validator_addr##*:}

#================================================================
# Check validator account balance is not less than 2 tokens
ValAccBalanceNT=$(echo "$Validator_Acc_Info" | awk '{print $2}')
if [[ $ValAccBalanceNT -lt $((1000000000 * 2)) ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Validator account balance is less than 2 tokens: $ValAccBalanceNT. To continue, you need to top up the account at least 2 tokens"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" \
        "$Tg_SOS_sign ###-ERROR(${SelfScriptName} line $LINENO): Validator account balance is less than 2 tokens: $ValAccBalanceNT. To continue, you need to top up the account at least 2 tokens" > /dev/null 2>&1
    exit 1
fi

#================================================================
# Get custodians info
if Custodians_Info=$(Get_Account_Custodians_Info "$Validator_addr");then
    read -r Total_Custodians Required_Signs <<< "$Custodians_Info"
    echo "---INFO: Total msig custodians: $Total_Custodians; Required signs: $Required_Signs"
else
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot get custodians info for $Validator_addr"
    exit 1
fi

#=================================================
# Get number of local keys
# shellcheck disable=SC2207
MSIG_KEY_FILES_ARRAY=($(ls "${KEYS_DIR}/${VALIDATOR_NAME}"*.keys.json))
declare -i LocalKeysQty=${#MSIG_KEY_FILES_ARRAY[@]}
echo "---INFO: Local keys qty: $LocalKeysQty"
RemoteSign=false
if [[ $LocalKeysQty -lt $Required_Signs ]];then
    RemoteSign=true
    echo "+++-WARNING(${SelfScriptName} line $LINENO): You have only $LocalKeysQty keyfiles, but required $Required_Signs signatures. Assume that you have to sign transactions remotely."
fi

#=================================================
# Addresses and vars for DePool mode
if [[ "$STAKE_MODE" == "depool" ]];then
    Depool_Name=$1
    if [[ -z $Depool_Name ]];then
        Depool_Name="depool"
        Depool_addr=$(cat "${KEYS_DIR}/${Depool_Name}.addr")
        if [[ -z $Depool_addr ]];then
            echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find DePool address file! ${KEYS_DIR}/${Depool_Name}.addr"
            exit 1
        fi
    else
        Depool_addr=$Depool_Name
        acc_fmt="$(echo "$Depool_addr" |  awk -F ':' '{print $2}')"
        [[ -z $acc_fmt ]] && Depool_addr=$(cat "${KEYS_DIR}/${Depool_Name}.addr")
    fi
    if [[ -z $Depool_addr ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find DePool address file! ${KEYS_DIR}/${Depool_Name}.addr"
        exit 1
    fi
    
    dpc_addr=${Depool_addr##*:}
    dpc_wc=${Depool_addr%%:*}
    if [[ ${#dpc_addr} -ne 64 ]] || [[ ${dpc_wc} -ne 0 ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Wrong DePool address! ${Depool_addr}"
        exit 1
    fi
    Current_Depool_Info="$(Get_DP_Info "${Depool_addr}")"
    dp_proxy0=$(echo "$Current_Depool_Info" | jq -r ".proxies[0]")
    dp_proxy1=$(echo "$Current_Depool_Info" | jq -r ".proxies[1]")
    if [[ -z $dp_proxy0 ]] || [[ -z $dp_proxy1 ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot find DePool proxies addresses for depool ${KEYS_DIR}/${Depool_Name}.addr"
        exit 1
    fi
fi

#=================================================
# Get Elector address, Elector type, and elector boc file in ${ELECTIONS_WORK_DIR}/${elector_addr##*:}.boc
if result=$(Get_Current_Elector_Type);then
    read -r elector_addr ELECTOR_TYPE <<< "$result"
else
    echoerr "###-ERROR(${FUNCNAME[0]} line $LINENO): Cannot get Elector type!"
    echo "$result"
    exit 1
fi

# ===============================================================
# Get unsend transactins in validator contract
echo -e "\n--- INFO: Check unsend transactions in validator contract..."
Trans_List="$(Get_MSIG_Trans_List "${Validator_addr}")"
declare -i Trans_QTY=$(echo "${Trans_List}" | jq -r ".transactions|length")
declare -i Exist_El_Trans_Qty=0
declare -i Exist_DP_Trans_Qty=0
declare -i Exist_Proxy0_Trans_Qty=0
declare -i Exist_Proxy1_Trans_Qty=0
if [[ ${Trans_QTY} -gt 0 ]];then
    Exist_El_Trans_Qty=$(echo "${Trans_List}" | jq -r "[.transactions[]|select(.dest == \"${elector_addr}\")]|length")
    if [[ "$STAKE_MODE" == "depool" ]];then
        Exist_DP_Trans_Qty=$(echo "${Trans_List}" | jq -r "[.transactions[]|select(.dest == \"${Depool_addr}\")]|length")
        Exist_Proxy0_Trans_Qty=$(echo "${Trans_List}" | jq -r "[.transactions[]|select(.dest == \"${dp_proxy0}\")]|length")
        Exist_Proxy1_Trans_Qty=$(echo "${Trans_List}" | jq -r "[.transactions[]|select(.dest == \"${dp_proxy1}\")]|length")
    fi
    echo "+++WARNING(${SelfScriptName} line $LINENO): You have unsigned transactions on the validator address!! Transactions: to elector: $Exist_El_Trans_Qty; To DePool: $Exist_DP_Trans_Qty"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" \
        "${Tg_Warn_sign} WARNING($SelfScriptName line $LINENO): You have unsigned transactions on the validator address!! Transactions: to elector: $Exist_El_Trans_Qty; To DePool: $Exist_DP_Trans_Qty" > /dev/null 2>&1
fi
{
    date +'%F %T %Z'
    printf "Total transactions qty:      %s\n" "${Trans_QTY}"
    printf "To Elector transactions qty: %s\n" "${Exist_El_Trans_Qty}"
    printf "To DePool transactions qty:  %s\n" "${Exist_DP_Trans_Qty}"
    printf "To Proxy0 transactions qty:  %s\n" "${Exist_Proxy0_Trans_Qty}"
    printf "To Proxy1 transactions qty:  %s\n" "${Exist_Proxy1_Trans_Qty}"
    echo
} | tee -a "${VALIDATOR_LOG_DIR}/transactions.log"

# ===============================================================
# If you have unsigned transactions, you need to sign them and send them to the network
if [[ $Exist_El_Trans_Qty -gt 0 ]] && [[ "$STAKE_MODE" == "msig" ]];then
    echo -e "\n+++-WARNING(${SelfScriptName} line $LINENO): : You have unsigned transactions to Elector on the validator address!! Sign it first before continue!"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" \
        "${Tg_Warn_sign} WARNING($SelfScriptName line $LINENO): You have unsigned transactions to Elector on the validator address!! Sign it first before continue!" > /dev/null 2>&1
    exit 3
fi
if [[ $Exist_DP_Trans_Qty -gt 0 ]] && [[ "$STAKE_MODE" == "depool" ]];then
    echo -e "\n+++-WARNING(${SelfScriptName} line $LINENO): : You have unsigned transactions to DePool on the validator address!! Sign it first before continue!"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" \
        "${Tg_Warn_sign} WARNING($SelfScriptName line $LINENO): You have unsigned transactions to DePool on the validator address!! Sign it first before continue!" > /dev/null 2>&1
    exit 4
fi

#===========================================================
# Check staking mode
################################################################################################
############### Recovery stake for msig staking mode ###########################################
################################################################################################
if [[ "$STAKE_MODE" == "msig" ]];then
    if [[ "$Work_Chain" != "-1" ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Staking mode: $STAKE_MODE; Validator address has to be in masterchain (-1:xx) !!!"
        exit 1
    fi

    echo "+++-WARNING(${SelfScriptName} line $LINENO): Staking mode is set to $STAKE_MODE. Preparation is recover stake only. Depool will not be ticked."
    if [[ $elections_id -eq 0 ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO):There is no elections now! Nothing to do!"
        exit 1
    else
        echo "${elections_id}; $(date +'%F %T %Z')" >> "${CURR_ELECTIONS_DIR}/${elections_id}.log"
    fi

    #=================================================
    # check availabylity to recover amount
    case ${ELECTOR_TYPE} in
        "fift")
            recover_amount=$($CALL_CLI runget --boc "${INPL_ELECTIONS_WORK_DIR}/${elector_addr##*:}.boc" compute_returned_stake "0x${Val_Adrr_HEX}" 2>&1 | \
                grep "Result:" | awk -F'"' '{print $2}')
            ;;
        "solidity")
            recover_amount=$($CALL_CLI run --boc "${INPL_ELECTIONS_WORK_DIR}/${elector_addr##*:}.boc" compute_returned_stake "{\"wallet_addr\":\"${Val_Adrr_HEX}\"}" --abi "${INPL_Elector_ABI}" 2>&1 | \
                grep -i "value0" | awk '{print $2}' | tr -d '"')
            ;;
        *)
            echo "###-ERROR(${SelfScriptName} line $LINENO): Unknown Elector type!"
            exit 1
            ;;
    esac
    
    recover_amount=$((recover_amount))
    echo "---INFO: recover_amount = ${recover_amount} nanotokens ( $((recover_amount/1000000000)) Tokens )"
    # =================================================
    # recover_amount=1
    if [ $recover_amount -gt 0 ]; then

        #=================================================
        # prepare recovery boc
        echo -n "---INFO: Prepare recovery request ..."
        if ! $CALL_CONS -c "recover_stake ${INPL_CURR_ELECTIONS_DIR}/recover-query.boc"; then
            echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot create recover query payload!!! Can't continue."
            exit 1
        fi
        recover_query_payload=$(base64 "${CURR_ELECTIONS_DIR}/recover-query.boc" |tr -d '\n')
        if [[ -z $recover_query_payload ]];then
            echo "###-ERROR(${SelfScriptName} line $LINENO): Recover query payload is empty!!"
            exit 1
        fi

        LC_OUTPUT="$($CALL_CLI message --raw --output ${INPL_CURR_ELECTIONS_DIR}/recover-msg.boc \
        --sign "${INPL_KEYS_DIR}/${MSIG_KEY_FILES_ARRAY[0]##*/}" ${SIG_ID_OPTION} \
        --abi "$INPL_SafeC_Wallet_ABI" \
        "$Validator_addr" submitTransaction \
        "{\"dest\":\"$elector_addr\",\"value\":1000000000,\"bounce\":true,\"allBalance\":false,\"payload\":\"$recover_query_payload\"}" 2>&1)"

        if ! echo "$LC_OUTPUT" | grep -iq 'Message saved to file';then
            echo -e "\n----------------------------------------------\n$LC_OUTPUT\n----------------------------------------------\n"
            echo "###-ERROR(${SelfScriptName} line $LINENO): ever-cli CANNOT create boc file!!! Can't continue."
            exit 1
        fi
        echo "---INFO:  DONE"

        #=================================================
        # Send request for recover stake to Elector
        ## 5x3 attempts to make trasaction
        for (( TryToSetEl=0; TryToSetEl <= 5; TryToSetEl++ ))
        do
            echo -n "---INFO: Send query to Elector... "
            #################
            if ! New_Elect_Trans_ID=$(Send_Message "${Validator_addr}" "${elector_addr}" "${INPL_CURR_ELECTIONS_DIR}/recover-msg.boc");then
            #################
                echo "###-ERROR(${SelfScriptName} line $LINENO): ALARM!!! Cannot make transaction for stake recover!!!" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
            else
                echo " DONE"
                echo "+++-INFO: Made transaction ID: $New_Elect_Trans_ID"
                break
            fi
        done
        #=================================================
        # Final checking
        #=================================================
        # Verifying that a transaction has been created 
        if [[ $Required_Signs -gt 1 ]];then
            Trans_List="$(Get_MSIG_Trans_List "${Validator_addr}")"
            New_Trans_Qty=$(( $(echo "$Trans_List" | jq -r "[.transactions[]|select(.dest == \"$elector_addr\")]|length") ))
            if [[ $New_Trans_Qty -gt $Exist_El_Trans_Qty ]];then
                Elect_Trans_ID=$(echo "$Trans_List" | jq -r ".transactions[]|select(.dest == \"$elector_addr\")|.id"|tail -n 1)
                if [[ $New_Elect_Trans_ID -ne $Elect_Trans_ID ]];then
                    echo "###-WARNING(${SelfScriptName} line $LINENO): Transaction from Send_Message function ($New_Elect_Trans_ID) is not equal to last transaction from list ($Elect_Trans_ID)"
                fi
                echo "---INFO(${SelfScriptName} line $LINENO): Making transaction for elections was done SUCCESSFULLY! Trnasaction ID: $Elect_Trans_ID You have to sign this transaction!!"| tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
                echo "Made transaction ID: $Elect_Trans_ID" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
                
                # Sign transaction by all keys which we have
                # $LocalKeysQty  $Required_Signs
                for (( i=0; i < LocalKeysQty; i++ )); do
                    echo "---INFO(${SelfScriptName} line $LINENO): Sign transaction for recover stake with ${MSIG_KEY_FILES_ARRAY[$i]}..."
                    Send_MSIG_Trans_Confirmation "$Elect_Trans_ID" "$Validator_addr" "${MSIG_KEY_FILES_ARRAY[$i]##*/}"
                    sleep $SLEEP_TIMEOUT
                done
                if ! $RemoteSign;then
                    # Check TransID is signed by all keys and send it to elector
                    Trans_List="$(Get_MSIG_Trans_List "${Validator_addr}")"
                    if [[ -z $(echo "$Trans_List" | jq -r ".transactions[]|select(.id == \"$Elect_Trans_ID\")") ]];then
                        echo "+++-INFO: Transaction to Elector for recover stake is signed and sent"
                    else
                        echo "Signs required: $Required_Signs"
                        echo "Signs made: $(echo "$Trans_List" | jq -r ".transactions[]|select(.id == \"$Elect_Trans_ID\")|.signsReceived")"
                        echo "###-ERROR(${SelfScriptName} line $LINENO): Transaction to Elector for recover stake is NOT signed by all keys!!!"
                    fi
                else
                    echo "Signs required: $Required_Signs"
                    echo "Signs made: $(echo "$Trans_List" | jq -r ".transactions[]|select(.id == \"$Elect_Trans_ID\")|.signsReceived")"
                    echo "+++WARNING: You have not enough keys to sign transaction $Elect_Trans_ID locally, so you have to sign it remotely"
                fi
            else
                echo "###-ERROR(${SelfScriptName} line $LINENO): Transaction does not made or timeout is too low!" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
            fi
        else
            #=================================================
            # Verifying that a transaction has been sent (for 1 custodian acc) by cheching change last transaction time
            Validator_Acc_Info="$(Get_Account_Info "${Validator_addr}")"
            declare -i Validator_Acc_LT_Sent=$(echo "$Validator_Acc_Info" | awk '{print $3}')
            if [[ $Validator_Acc_LT_Sent -gt $Validator_Acc_LT ]];then
                echo "---INFO(${SelfScriptName} line $LINENO): Sending transaction for recover stake was done SUCCESSFULLY!"| tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log" 
            else
                echo "###-ERROR(${SelfScriptName} line $LINENO): Sending transaction for stake recover FAILED!!!" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
                Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_SOS_sign ###-ERROR(${SelfScriptName} line $LINENO): Sending transaction for elections FAILED!!!" > /dev/null 2>&1
            fi
        fi
    else
        echo "--- INFO(${SelfScriptName} line $LINENO): Nothing to recover."
    fi
    echo
    echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
    if [[ $NODE_ROLE != "validator" ]];then
        echo "+++-WARNING(${SelfScriptName} line $LINENO): NODE_ROLE is not 'validator' ($NODE_ROLE) in env.sh. Exit with error."
        echo "================================================================================================"
        exit 1
    fi
    echo "================================================================================================"
    exit 0
fi
################################################################################################
########## Continue to Tik depool ########
################################################################################################
# Continue to Tik depool
if [[ $elections_id -eq 0 ]];then
    echo "+++-WARN(${SelfScriptName} line $LINENO):There is no elections now! Just check balances and exit!"
else
    echo "${elections_id}; $(date +'%F %T %Z')" >> "${CURR_ELECTIONS_DIR}/${elections_id}.log"
fi

#=================================================
# Check both proxies has enough balance to operate, and replenish if no

Proxy0_Info="$(Get_Account_Info $dp_proxy0)"
Proxy1_Info="$(Get_Account_Info $dp_proxy1)"

Proxy0_Bal=$(( $(echo "$Proxy0_Info" |awk '{print $2}') ))      # nanotokens
Proxy1_Bal=$(( $(echo "$Proxy1_Info" |awk '{print $2}') ))      # nanotokens

# topup Proxy0 if needed
echo "---INFO(${SelfScriptName} line $LINENO): Proxy0 balance is $( echo $Proxy0_Bal | awk '{print $1/1000000000}') tokens"
if [[ $Proxy0_Bal -lt $TOPUP_THRESHOLD ]];then
    echo "+++-WARNING(${SelfScriptName} line $LINENO): Proxy0 has balance less 2 tokens!! I will topup it with 5 tokens from ${VALIDATOR_NAME} account" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
    Send_msg_toTelBot "$VALIDATOR_NAME Server: DePool Tik:" \
        "${Tg_Warn_sign} WARNING(${SelfScriptName} line $LINENO): Proxy0 has balance less 2 tokens!! I will topup it with 5 tokens from ${VALIDATOR_NAME} account" > /dev/null 2>&1
    
    top_app_account "${dp_proxy0}" $((5 * 1000000000)) "${Validator_addr}" | tee -a "${CURR_ELECTIONS_DIR}/proxy0_topup.log"
    if [[ ${PIPESTATUS[0]} -ne 0 ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot topup Proxy0 account with 5 tokens from ${VALIDATOR_NAME} account"
        Send_msg_toTelBot "$VALIDATOR_NAME Server: DePool Tik:" \
            "${Tg_Error_sign} ERROR(${SelfScriptName} line $LINENO): Cannot topup Proxy0 account with 5 tokens from ${VALIDATOR_NAME} account" > /dev/null 2>&1
    fi
fi

# topup Proxy1 if needed
echo "---INFO(${SelfScriptName} line $LINENO): Proxy1 balance is $( echo $Proxy1_Bal | awk '{print $1/1000000000}') tokens"
if [[ $Proxy1_Bal -lt $TOPUP_THRESHOLD ]];then
    echo "+++-WARNING(${SelfScriptName} line $LINENO): Proxy1 has balance less 2 tokens!! I will topup it with 5 tokens from ${VALIDATOR_NAME} account" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
    Send_msg_toTelBot "$VALIDATOR_NAME Server: DePool Tik:" \
        "${Tg_Warn_sign} WARNING(${SelfScriptName} line $LINENO): Proxy1 has balance less 2 tokens!! I will topup it with 5 tokens from ${VALIDATOR_NAME} account" > /dev/null 2>&1
    
    top_app_account "${dp_proxy1}" $((5 * 1000000000)) "${Validator_addr}" | tee -a "${CURR_ELECTIONS_DIR}/proxy1_topup.log"
    if [[ ${PIPESTATUS[0]} -ne 0 ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot topup Proxy1 account with 5 tokens from ${VALIDATOR_NAME} account"
        Send_msg_toTelBot "$VALIDATOR_NAME Server: DePool Tik:" \
            "${Tg_Error_sign} ERROR(${SelfScriptName} line $LINENO): Cannot topup Proxy1 account with 5 tokens from ${VALIDATOR_NAME} account" > /dev/null 2>&1
    fi
fi

#=================================================
# Check DePool has enough balance to operate, and replenish if no
# ------------------------------------------------
# check depool contract status
echo -e "\n--- INFO: Check DePool contract status..."
echo "Depool address: $Depool_addr"
Depool_Info="$(Get_Account_Info "$Depool_addr")"
Depool_Acc_State=$(echo "$Depool_Info" |awk '{print $1}')
if [[ "$Depool_Acc_State" == "None" ]];then
    echo -e "${BoldText}${RedBack}###-ERROR(${SelfScriptName} line $LINENO): Depool Account does not exist! (no tokens, no code, nothing)${NormText}"
    echo
    exit 1
elif [[ "$Depool_Acc_State" == "Uninit" ]];then
    echo -e "${BoldText}${RedBack}###-ERROR(${SelfScriptName} line $LINENO): Depool Account does not deployed.${NormText}"
    echo "Has balance : $(echo "$Depool_Info" |awk '{print $2}')"
    echo
    exit 1
fi

# get info from DePool contract state
Depool_Bal=$(( $(echo "$Depool_Info" |awk '{print $2}') ))      # nanotokens
DP_balanceThreshold=$(( $(echo "$Current_Depool_Info"|jq -r '.balanceThreshold') - 3000000000))       # nanotokens
DP_Above_Thresh=$(( 10 * 1000000000))

# topup DePool if needed
if [[ $Depool_Bal -lt $DP_balanceThreshold ]];then
    # Calculate replenish amount
    Replenish_Amount=$(( DP_balanceThreshold - Depool_Bal + DP_Above_Thresh ))
    echo "+++-WARNING(${SelfScriptName} line $LINENO): DePool has balance less $((DP_balanceThreshold / 1000000000)) tokens!! I will topup it with $((DP_Above_Thresh / 1000000000)) tokens from ${VALIDATOR_NAME} account" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
    Send_msg_toTelBot "$VALIDATOR_NAME Server: DePool Tik:" \
        "${Tg_Warn_sign} WARNING(${SelfScriptName} line $LINENO): DePool has balance less $((DP_balanceThreshold / 1000000000)) tokens!! I will topup it with $((Replenish_Amount / 1000000000)) tokens from ${VALIDATOR_NAME} account" > /dev/null 2>&1
    
    # Prepare replenish transaction
    ReplenishFile="${CURR_ELECTIONS_DIR}/depool_replenish.boc"
    INPL_ReplenishFile="${INPL_CURR_ELECTIONS_DIR}/depool_replenish.boc"
    
    # Make message for replenish DePool
    TC_OUTPUT="$($CALL_CLI message --raw --output "${INPL_ReplenishFile}" \
    --sign "${INPL_KEYS_DIR}/${MSIG_KEY_FILES_ARRAY[0]##*/}" ${SIG_ID_OPTION} \
    --abi "$INPL_SafeC_Wallet_ABI" \
    "$Validator_addr" submitTransaction \
    "{\"dest\":\"$Depool_addr\",\"value\":$Replenish_Amount,\"bounce\":true,\"allBalance\":false,\"payload\":\"$DePoolReplenish_Payload\"}")"
    
    # Check if boc file created
    if [[ ! -f "${ReplenishFile}" ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot create file ${ReplenishFile}"
        echo "TC_OUTPUT: $TC_OUTPUT"
        Send_msg_toTelBot "$VALIDATOR_NAME Server: DePool Tik:" \
            "${Tg_Error_sign} ERROR(${SelfScriptName} line $LINENO): Cannot create file ${ReplenishFile}" > /dev/null 2>&1
    fi

    # Send replenish transaction
    Send_File_To_BC "${INPL_ReplenishFile}" 

    # Wait for transaction appear in contract inside timeout. 
    DP_Trans_ID=""
    for (( i=0; i < 6; i++ )); do
        sleep $LC_Send_MSG_Timeout
        Trans_List="$(Get_MSIG_Trans_List "${Validator_addr}")"
        New_DP_Trans_Qty=$(echo "${Trans_List}" | jq -r "[.transactions[]|select(.dest == \"${Depool_addr}\")]|length")
        if [[ $Exist_DP_Trans_Qty -gt $New_DP_Trans_Qty ]];then
            # Get Transaction ID
            DP_Trans_ID=$(echo "$Trans_List" | jq -r ".transactions[]|select(.dest == \"$Depool_addr\")|.id"|tail -n 1)
            break
        fi
    done
    if [[ -z $DP_Trans_ID ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot find transaction ID for replenish DePool account" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        Send_msg_toTelBot "$VALIDATOR_NAME Server: DePool Tik:" \
            "${Tg_Error_sign} ERROR(${SelfScriptName} line $LINENO): Cannot find transaction ID for replenish DePool account" > /dev/null 2>&1
    else
        # Sign replenish transaction
        "${SCRIPT_DIR}/Sign_Trans.sh" "${VALIDATOR_NAME}" "$DP_Trans_ID" 
    fi
fi

##############################################################################
################  Send TIK query to DePool ###################################
##############################################################################

if [[ ${elections_id} -eq 0 ]];then
    echo "---INFO(${SelfScriptName} line $LINENO): There is no elections now! Nothing to do!"
    exit 1
fi

#=================================================
# Check DePool is set to current elections already
Depool_Rounds_Info="$(Get_DP_Rounds "$Depool_addr")"
Curr_Rounds_Info="$(Rounds_Sorting_by_ID "$Depool_Rounds_Info")"
Curr_DP_Elec_ID=$(( $(echo "$Curr_Rounds_Info" |jq -r '.[1].supposedElectedAt'| xargs printf "%d\n") ))
if [[ $elections_id -eq $Curr_DP_Elec_ID ]];then
    echo "---INFO: DePool is already set to current elections ID $Curr_DP_Elec_ID."| tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
    exit 0
fi

#=================================================
# Check compute_returned_stake in elector contract for both proxies
echo -e "\n---INFO: Check compute_returned_stake in elector contract for both proxies..."
Proxy0_hex=$(echo "${dp_proxy0}" | cut -d ':' -f 2)
Proxy1_hex=$(echo "${dp_proxy1}" | cut -d ':' -f 2)
Proxy0_Recover_Amount=$($CALL_CLI -j runget --boc "${INPL_ELECTIONS_WORK_DIR}/${elector_addr##*:}.boc" compute_returned_stake "0x${Proxy0_hex}" 2>&1 | \
    jq -r '.value0')
Proxy1_Recover_Amount=$($CALL_CLI -j runget --boc "${INPL_ELECTIONS_WORK_DIR}/${elector_addr##*:}.boc" compute_returned_stake "0x${Proxy1_hex}" 2>&1 | \
    jq -r '.value0')
Proxy0_Recover_Amount=$((Proxy0_Recover_Amount))
Proxy1_Recover_Amount=$((Proxy1_Recover_Amount))
echo "   Proxy0 recover amount = ${Proxy0_Recover_Amount} nanotokens ( $((Proxy0_Recover_Amount/1000000000)) Tokens )"
echo "   Proxy1 recover amount = ${Proxy1_Recover_Amount} nanotokens ( $((Proxy1_Recover_Amount/1000000000)) Tokens )"
echo

#=================================================
# Make boc message to tik depool
LC_OUTPUT="$($CALL_CLI message --raw --output "${INPL_CURR_ELECTIONS_DIR}/tik-msg.boc" \
    --sign "${INPL_KEYS_DIR}/${MSIG_KEY_FILES_ARRAY[0]##*/}" ${SIG_ID_OPTION} \
    --lifetime 600 \
    --abi "${INPL_SafeC_Wallet_ABI}" \
    "$Validator_addr" submitTransaction \
    "{\"dest\":\"$Depool_addr\",\"value\":${NANOSTAKE},\"bounce\":true,\"allBalance\":false,\"payload\":\"$DePoolTik_Payload\"}")"
echo -e "\n--------------------------------------------------------------------------"
echo "$LC_OUTPUT"
echo -e "--------------------------------------------------------------------------\n"
# Check that the message is saved
if ! echo "${LC_OUTPUT}" | grep -iq 'Message saved to file'; then
    echoerr "###-ERROR(${FUNCNAME[0]} line $LINENO): Failed to create boc file!!! Can't continue."
    return 1
fi
Tik_Message_ID="$(echo "${LC_OUTPUT}" | grep 'MessageId' | awk '{print $2}')"
echo "+++-INFO(${SelfScriptName} line $LINENO): Tik message was created with ID: $Tik_Message_ID"

#=================================================
# Send tick to Depool
## 5x3 attempts to make trasaction
for (( TryToSetEl=0; TryToSetEl <= 5; TryToSetEl++ ));do
    echo -n "---INFO: Send tik query to Depool... "
    #################
    if New_Elect_Trans_ID=$(Send_Message "${Validator_addr}" "${Depool_addr}" "${INPL_CURR_ELECTIONS_DIR}/tik-msg.boc");then
    #################
        echo " DONE"
        if [[ $New_Elect_Trans_ID -eq 0 ]];then
            echo "---INFO(${SelfScriptName} line $LINENO): Sending transaction  was done SUCCESSFULLY!"
            exit 0
        fi
        break
    else
        echo "###-ERROR(${SelfScriptName} line $LINENO): Error during sending message to DePool. Repeat sending..."
    fi
done

if [[ $New_Elect_Trans_ID -lt 0 ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot make transaction for elections!!!"
    exit 1
else
    echo "+++-INFO(${SelfScriptName} line $LINENO): Tik transaction to DePool with id: $New_Elect_Trans_ID need to be signed."
fi

#=================================================
# Sign transaction by all keys which we have if needed
if [[ $New_Elect_Trans_ID -gt 1 ]];then
    Current_Trans_List="$(Get_MSIG_Trans_List "${Validator_addr}")"
    echo "--- Current transactions list:"
    echo $Current_Trans_List|jq
    echo "---------------------------------------------"
    for (( i=0; i < LocalKeysQty; i++ )); do
        echo "---INFO: Sign Tik transaction $New_Elect_Trans_ID with ${MSIG_KEY_FILES_ARRAY[$i]}..."
        Send_MSIG_Trans_Confirmation "$New_Elect_Trans_ID" "$Validator_addr" "${MSIG_KEY_FILES_ARRAY[$i]##*/}"
        sleep $LC_Send_MSG_Timeout
    done
fi

#=================================================





# for (( TryToSetEl=0; TryToSetEl <= 5; TryToSetEl++ )); do
#     echo -n "---INFO: Make boc message to tik depool ..."
#     if ! TICK_Transaction_ID=$(Make_Tik_BOC_file);then
#         echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot create boc file for Tik DePool!!! Can't continue."
#         exit 1
#     fi

#     echo " DONE. Transaction ID: $TICK_Transaction_ID"
#     echo -n "---INFO: Send Tik query to DePool ..."
#     #################
#     Attempts_to_send=$(( $(Send_Tik | tail -n 1) ))
#     #################
#     echo " DONE"
#     [[ $Attempts_to_send -le 0 ]] && echo "###-=ERROR(${SelfScriptName} line $LINENO): ALARM!!! DePool DOES NOT CRANKED UP!!!" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"

#     Depool_Rounds_Info="$(Get_DP_Rounds "$Depool_addr")"
#     Curr_Rounds_Info="$(Rounds_Sorting_by_ID "$Depool_Rounds_Info")"
#     Curr_DP_Elec_ID=$(( $(echo "$Curr_Rounds_Info" |jq -r '.[1].supposedElectedAt'| xargs printf "%d\n") ))

#     if [[ $elections_id -gt 0 ]];then
#         echo "---INFO: Checking DeePool is set to current elections..."| tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
#         echo "Elections ID in DePool: $Curr_DP_Elec_ID"| tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
#         [[ $elections_id -eq $Curr_DP_Elec_ID ]] && break
#         echo "+++-WARNING: Not set yet. Try #${TryToSetEl}..."| tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
#         sleep $SLEEP_TIMEOUT
#     else
#         break
#     fi 
# done

# if [[ $elections_id -ne $Curr_DP_Elec_ID ]] && [[ $elections_id -gt 0 ]]; then
#     echo "###-ERROR(${SelfScriptName} line $LINENO): Current elections ID from elector $elections_id ($(TD_unix2human "$elections_id")) is not equal elections ID from DP: $Curr_DP_Elec_ID ($(TD_unix2human "$Curr_DP_Elec_ID"))" \
#         | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
#     echo "---INFO: $SelfScriptName END $(date +%s) / $(date)"
#     date +"###-ERROR(${SelfScriptName} line $LINENO): %F %T %Z Tik DePool FALED!" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
#     Send_msg_toTelBot "$VALIDATOR_NAME Server: DePool Tik:" \
#         "$Tg_SOS_sign ALARM!!! Current elections ID from elector $elections_id ($(TD_unix2human $elections_id)) is not equal elections ID from DePool: $Curr_DP_Elec_ID ($(TD_unix2human $Curr_DP_Elec_ID))" > /dev/null 2>&1
# else
#     echo "---INFO:      Election ID: $elections_id" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
#     echo "Elections ID in DePool: $Curr_DP_Elec_ID" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
#     date +"---INFO: %F %T %Z DePool is set for current elections." | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
# fi
#         if [[ $NODE_ROLE != "validator" ]];then
#             echo "+++-WARNING(${SelfScriptName} line $LINENO): NODE_ROLE is not 'validator' ($NODE_ROLE) in env.sh. Exit with error."
#             exit 1
#         fi

echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
    if [[ $NODE_ROLE != "validator" ]];then
        echo "+++-WARNING(${SelfScriptName} line $LINENO): NODE_ROLE is not 'validator' ($NODE_ROLE). Exit with error."
        echo "================================================================================================"
        exit 1
    fi
echo "================================================================================================"
exit 0
