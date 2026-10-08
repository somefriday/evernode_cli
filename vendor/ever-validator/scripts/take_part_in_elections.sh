#!/usr/bin/env bash
# shellcheck disable=SC2155,2031

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

####################################
# we can't work on desynced node
declare -ir TIMEDIFF_MAX=20
MAX_FACTOR=${MAX_FACTOR:=3}
####################################

echo
echo "#################################### Participate script ########################################"
SelfScriptName=$(basename "$0")
echo "--- INFO: ${SelfScriptName} BEGIN $(date +%s) / $(date  +'%F %T %Z')"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
if ! source "${SCRIPT_DIR}/env.sh"; then
    echo "###-ERROR(${SelfScriptName}: line $LINENO): Can't load env.sh"
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
source "${SCRIPT_DIR}/functions.shinc"

#=================================================
echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo
#===========================================================
# Check staking mode and node type
case "$STAKE_MODE" in
    depool)
        echo "+++-WARNING(${SelfScriptName} line $LINENO): Staking mode is set to $STAKE_MODE"
        ;;
    msig)
        echo "+++-WARNING(${SelfScriptName} line $LINENO): Staking mode is set to $STAKE_MODE"
        ;;
    *)
        echo "###-ERROR(${SelfScriptName} line $LINENO): Unknown staking mode $STAKE_MODE. Check STAKE_MODE in env.sh "
        ;;
esac

#=================================================
# Load addresses and set variables
Validator_addr=$(cat "${KEYS_DIR}/${VALIDATOR_NAME}.addr")
Work_Chain=$(echo "${Validator_addr}" | cut -d ':' -f 1)
if [[ -z $Validator_addr ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find validator address! ${KEYS_DIR}/${VALIDATOR_NAME}.addr"
    exit 1
fi
if [[ ! -f ${SafeC_Wallet_ABI} ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): ${SafeC_Wallet_ABI} NOT FOUND! Can't continue"
    exit 1
fi
if [[ "$STAKE_MODE" == "depool" ]];then
    Depool_addr=$(cat "${KEYS_DIR}/depool.addr")
    if [[ -z $Depool_addr ]];then
       echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find depool address! ${KEYS_DIR}/depool.addr"
       exit 1
    fi
else
    if [[ "$Work_Chain" != "-1" ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Staking mode: $STAKE_MODE; Validator address must be in the masterchain (-1:xx) !!!"
        exit 1
    fi
fi

echo "---INFO: validator account address: $Validator_addr"
[[ "$STAKE_MODE" == "depool" ]] && echo "---INFO: depool   contract address: $Depool_addr"

#=================================================
# check validator account
declare -i Validator_Acc_LT  Validator_Acc_Balance_nT
if result=$(Get_Account_Info "${Validator_addr}");then
    read -r Validator_Acc_Status Validator_Acc_Balance_nT Validator_Acc_LT <<< "$result"
else
    echoerr "###-ERROR(${FUNCNAME[0]} line $LINENO): Cannot get Validator account info!"
    echo "$result"
    exit 1
fi
if [[ "$Validator_Acc_Status" != "Active" ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Validator msig status is NOT 'Active' (not deployed)!"
    exit 1
fi
#================================================================
# Check validator account balance is not less than 2 tokens
if [[ $Validator_Acc_Balance_nT -lt $((1000000000 * 2)) ]];then
    Val_Bal_Tokens=$(echo "scale=3; $Validator_Acc_Balance_nT / 1000000000" | $CALL_BC)
    echo "###-ERROR(${SelfScriptName} line $LINENO): Validator account balance ($Val_Bal_Tokens) is less than 2 tokens. To continue, you need to top up the account at least 2 tokens"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" \
        "$Tg_SOS_sign ###-ERROR(${SelfScriptName} line $LINENO): Validator account balance ($Val_Bal_Tokens) is less than 2 tokens. To continue, you need to top up the account at least 2 tokens" > /dev/null 2>&1
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
# Check validator keys
msig_public=$(jq -r '.public' "${MSIG_KEY_FILES_ARRAY[0]}")
msig_secret=$(jq -r '.secret' "${MSIG_KEY_FILES_ARRAY[0]}")
if [[ -z $msig_public ]] || [[ -z $msig_secret ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find validator public and/or secret key in ${MSIG_KEY_FILES_ARRAY[0]}"
    exit 1
fi

#=================================================
# Check node sync
# masterchain timediff
MC_TIME_DIFF=$(Get_TimeDiff|awk '{print $1}')
if [[ $MC_TIME_DIFF -gt $TIMEDIFF_MAX ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Your node is not synced with MC. Wait until MC sync (<$TIMEDIFF_MAX) Current MC timediff: $MC_TIME_DIFF"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" \
        "$Tg_SOS_sign ###-ERROR(${SelfScriptName} line $LINENO): Your node is not synced. Wait until MC sync (<$TIMEDIFF_MAX) Current MC timediff: $MC_TIME_DIFF" > /dev/null 2>&1
    exit 1
fi
echo "---INFO: Current MC TimeDiff: $MC_TIME_DIFF"

# shards timediff (by worst shard)
SH_TIME_DIFF=$(Get_TimeDiff|awk '{print $2}')
if [[ $SH_TIME_DIFF -gt $TIMEDIFF_MAX ]];then
    echo -e "${YellowBack}${BoldText}###-WARNING(${SelfScriptName} line $LINENO): Your node is not synced with WORKCHAIN. Wait for all shards to sync or your accounts may not be accessible (<$TIMEDIFF_MAX) Current shards (by worst shard) timediff: $SH_TIME_DIFF${NormText}"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" \
        "$Tg_SOS_sign ###-ERROR(${SelfScriptName} line $LINENO): Your node is not synced with WORKCHAIN. Wait for all shards to sync or your accounts may not be accessible (<$TIMEDIFF_MAX) Current shards (by worst shard) timediff: $SH_TIME_DIFF" > /dev/null 2>&1
    # exit 1
else
    echo "---INFO: Current WC TimeDiff: $SH_TIME_DIFF"
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

#=================================================
# get elections ID from elector
declare -i elections_id
elections_id=$(Get_Current_Elections_ID)
echo "---INFO:      Election ID: $elections_id"
if [[ $elections_id -eq 0 ]];then
    echo
    echo "###-ERROR(${SelfScriptName} line $LINENO): There are NO elections now! Wait for the next elections."
    echo
    exit 1
fi
CURR_ELECTIONS_DIR="${ELECTIONS_WORK_DIR}/${elections_id}"
INPL_CURR_ELECTIONS_DIR="${INPL_ELECTIONS_WORK_DIR}/${elections_id}"
[[ ! -d "${CURR_ELECTIONS_DIR}" ]] && mkdir -p "${CURR_ELECTIONS_DIR}" && chmod ugo+rw "${CURR_ELECTIONS_DIR}"
if [[ -f "${CURR_ELECTIONS_DIR}/${elections_id}_elector-confirmed-bid.txt" ]];then
    echo "---INFO: We have already participated in these elections: ($elections_id)"
    exit 1
fi

#=================================================
# check depool contract status
if [[ "$STAKE_MODE" == "depool" ]];then
    declare -i Depool_LT Depool_Balance
    if result=$(Get_Account_Info "${Depool_addr}");then
        read -r Depool_Acc_State Depool_Balance Depool_LT <<< "$result"
    else
        echoerr "###-ERROR(${FUNCNAME[0]} line $LINENO): Cannot get Validator account info!"
        echo "$result"
        exit 1
    fi
    if [[ "$Depool_Acc_State" == "None" ]];then
        echo -e "${BoldText}${RedBack}###-ERROR(${SelfScriptName} line $LINENO): Depool Account does not exist! (no tokens, no code, nothing)${NormText}"
        echo
        exit 1
    elif [[ "$Depool_Acc_State" == "Uninit" ]];then
        echo -e "${BoldText}${RedBack}###-ERROR(${SelfScriptName} line $LINENO): Depool Account does not deployed.${NormText}"
        echo "Has balance : $Depool_Balance"
        echo "Last operation time: $(echo "$Depool_LT" | awk '{ print strftime("%Y-%m-%d %H:%M:%S", $1)}')"
        echo
        exit 1
    fi

    #=================================================
    # Check that validator is owner of the DePool
    if ! Current_Depool_Info=$(Get_DP_Info "$Depool_addr");then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot get DePool info. Can't continue. Exit"
        exit 1
    fi
    dp_val_wal="$(echo "$Current_Depool_Info" | jq -r ".validatorWallet")"
    if [[ "$dp_val_wal" != "$Validator_addr" ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Validator account is NOT owner of the DePool!!! Staking impossible!"
        exit 1
    fi
    
    # Get proxy addresses from depool contract
    dp_proxy0=$(echo "$Current_Depool_Info" | jq -r ".proxies[0]")
    dp_proxy1=$(echo "$Current_Depool_Info" | jq -r ".proxies[1]")
    if [[ -z $dp_proxy0 ]] || [[ -z $dp_proxy1 ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot get proxies from depool contract. Can't continue. Exit" 
        exit 1
    fi
    echo "${dp_proxy0}" > "${KEYS_DIR}/proxy0.addr"
    echo "${dp_proxy1}" > "${KEYS_DIR}/proxy1.addr"

    #=================================================
    # Check DePool ready for elections
    # Get DePool rounds info
    if ! Depool_Rounds_Info="$(Get_DP_Rounds "$Depool_addr")";then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot get DePool rounds info. Can't continue. Exit"
        exit 1
    fi
    # Get current round info
    Curr_Rounds_Info="$(Rounds_Sorting_by_ID "$Depool_Rounds_Info")"
    Curr_DP_Elec_ID=$(echo "$Curr_Rounds_Info" | jq -r ".[1].supposedElectedAt" | xargs printf "%d\n")

    # Check that elections ID from elector and DePool are equal
    if [[ $elections_id -ne $Curr_DP_Elec_ID ]]; then
        echo "###-ALARM(${SelfScriptName} line $LINENO): Current elections ID from elector $elections_id ($(TD_unix2human "$elections_id")) is not equal elections ID from DP: $Curr_DP_Elec_ID ($(TD_unix2human "$Curr_DP_Elec_ID"))" \
            | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        # Try to prepare depool for elections one more time
        echo "###- I run prepare_elections.sh for last chance..." | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        "${SCRIPT_DIR}/prepare_elections.sh"
    fi

    # Check that DePool is ready for elections
    Depool_Rounds_Info="$(Get_DP_Rounds "$Depool_addr")"
    Curr_Rounds_Info="$(Rounds_Sorting_by_ID "$Depool_Rounds_Info")"
    Curr_DP_Elec_ID=$(echo "$Curr_Rounds_Info" | jq -r ".[1].supposedElectedAt" | xargs printf "%d\n")

    # Check that elections ID from elector and DePool are equal
    if [[ $elections_id -ne $Curr_DP_Elec_ID ]] && [[ $elections_id -gt 0 ]]; then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Current elections ID from elector $elections_id ($(TD_unix2human "$elections_id")) is not equal elections ID from DP: $Curr_DP_Elec_ID ($(TD_unix2human "$Curr_DP_Elec_ID"))" \
            | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        echo "---INFO: $SelfScriptName END $(date +%s) / $(date)"
        date +"---INFO: %F %T %Z Tik DePool FALED!" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        # Send ALARM message to Telegram
        Send_msg_toTelBot "$VALIDATOR_NAME Server: DePool Tik:" \
            "$Tg_SOS_sign ALARM!!! Current elections ID from elector $elections_id ($(TD_unix2human "$elections_id")) is not equal elections ID from DePool: $Curr_DP_Elec_ID ($(TD_unix2human "$Curr_DP_Elec_ID"))" > /dev/null 2>&1
        exit 1
    fi

    #=================================================
    # Determine DePool proxy address for current elections
    echo "Elections ID in depool: $Curr_DP_Elec_ID"
    Curr_DP_Round_ID=$(echo  "$Curr_Rounds_Info" | jq -r ".[1].id" | xargs printf "%d\n")
    Proxy_ID=$((Curr_DP_Round_ID % 2))
    File_Round_Proxy="$(cat "${KEYS_DIR}/proxy${Proxy_ID}.addr")"
    echo "Proxy addr   from file: $File_Round_Proxy" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
    [[ -z $File_Round_Proxy ]] && echo "###-ERROR(${SelfScriptName} line $LINENO) Cannot get proxy for this round from file. Can't continue. Exit" && exit 1
    DP_Round_Proxy="$(echo "$Current_Depool_Info"|jq -r ".proxies[$Proxy_ID]")"
    echo "Proxy addr from depool: $DP_Round_Proxy" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
    [[ -z $DP_Round_Proxy ]] && echo "###-ERROR(${SelfScriptName} line $LINENO) Cannot get proxy for this round from depool contract. Can't continue. Exit" && exit 1
fi

#=================================================================
# Checking if you have already participated
echo "---INFO: Checking if you have already participated in this elections ($elections_id)"
declare -i ADNL_Stake ADNL_Time ADNL_Max_Factor
# Check node ADNL present in Elector's participants list
# "CurrADNL Curr_ID Next_ADNL Next_ID"
if ! Engine_ADNLs=$(Get_Engine_ADNL); then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot get Engine ADNL info! Chech node configuration!"
    exit 1
fi
if [[ "$Engine_ADNLs" != "null" ]];then
    # if it is first time participation in elections Next_ADNL_Key will be first in the list
    Next_ADNL_Key=$(echo "$Engine_ADNLs"|awk '{print $3}')
    [[ -z $Next_ADNL_Key ]] && Next_ADNL_Key=$(echo "$Engine_ADNLs"|awk '{print $1}') # in case it has not prev keys
    # Looking for ADNL in Elector's participants list
    #   "stake time max_factor ElPubKey" - if found
    if ! ADNL_Found="$(Elector_ADNL_Search "$Next_ADNL_Key")";then
        error_code=$?
        # Parse error code
        case $error_code in
            1) echo "###-ERROR(${SelfScriptName} line $LINENO): Wrong ADNL key: $Next_ADNL_Key." ;;
            2) echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot get Elector type!" ;;
            3) echo "###-ERROR(${SelfScriptName} line $LINENO): Elections is closed" ;;
            4) echo "###-ERROR(${SelfScriptName} line $LINENO): ADNL not found in Elector's participants list" ;;
            5) echo "###-ERROR(${SelfScriptName} line $LINENO): Unknown Elector type: $ELECTOR_TYPE" ;;
        esac
    else
        # shellcheck disable=SC2034     
        read -r ADNL_Stake ADNL_Time ADNL_Max_Factor You_PubKey <<< "$ADNL_Found"
    fi
    if [[ "$ADNL_Found" != "absent" ]];then
        echo
        echo "---INFO: You participate already in this elections ($elections_id)" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}_elector-confirmed-bid.txt"
        Your_Stake=$((ADNL_Stake / 1000000000))
        echo "You public key in Elector: $You_PubKey"  | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}_elector-confirmed-bid.txt"
        echo "You will start validate from $(TD_unix2human "$elections_id")" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}_elector-confirmed-bid.txt"
        echo "-!-!-INFO: Your stake: $Your_Stake with ADNL: $(echo "$Next_ADNL_Key" | tr "[:upper:]" "[:lower:]")" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}_elector-confirmed-bid.txt"
        echo
        exit 0
    fi
else
    echo "---INFO: You have not participated in any elections yet. Keys list in node config is empty."
fi

#=================================================================
# Check that you have unsigned transactions on the validator address
if ! Trans_List="$(Get_MSIG_Trans_List "${Validator_addr}")";then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot get transactions list from validator contract. Can't continue. Exit"
    exit 1
fi
declare -i Trans_QTY=$(echo "$Trans_List" | jq -r ".transactions|length")
declare -i Exist_El_Trans_Qty=0
declare -i Exist_DP_Trans_Qty=0
declare -i Exist_Proxy0_Trans_Qty=0
declare -i Exist_Proxy1_Trans_Qty=0
if [[ $Trans_QTY -gt 0 ]];then
    [[ "$STAKE_MODE" == "msig" ]]   && Exist_El_Trans_Qty=$(echo "$Trans_List" | jq -r "[.transactions[]|select(.dest == \"$elector_addr\")]|length")
    if [[ "$STAKE_MODE" == "depool" ]];then
        Exist_DP_Trans_Qty=$(echo "$Trans_List" | jq -r "[.transactions[]|select(.dest == \"$Depool_addr\")]|length")
        Exist_Proxy0_Trans_Qty=$(echo "$Trans_List" | jq -r "[.transactions[]|select(.dest == \"$dp_proxy0\")]|length")
        Exist_Proxy1_Trans_Qty=$(echo "$Trans_List" | jq -r "[.transactions[]|select(.dest == \"$dp_proxy1\")]|length")
    fi
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

case "$STAKE_MODE" in
    msig)
        if [[ $Exist_El_Trans_Qty -gt 0 ]];then
            echo "###-ERROR(${SelfScriptName} line $LINENO): You have unsigned transactions to Elector. Wait until they will be confirmed."
            Send_msg_toTelBot "$VALIDATOR_NAME Server" \
                "${Tg_Warn_sign} WARNING($SelfScriptName line $LINENO): You have unsigned transactions to Elector. Wait until they will be confirmed." > /dev/null 2>&1
            exit 3
        fi
        ;;
    depool)
        if [[ $Exist_DP_Trans_Qty -gt 0 || $Exist_Proxy0_Trans_Qty -gt 0 || $Exist_Proxy1_Trans_Qty -gt 0 ]];then
            echo "###-ERROR(${SelfScriptName} line $LINENO): You have unsigned transactions to DePool or Proxies. Wait until they will be confirmed."
            Send_msg_toTelBot "$VALIDATOR_NAME Server" \
                "${Tg_Warn_sign} WARNING($SelfScriptName line $LINENO): You have unsigned transactions to DePool. Wait until they will be confirmed." > /dev/null 2>&1
            exit 4
        fi
        ;;
    *)
        echo "###-ERROR(${SelfScriptName} line $LINENO): Unknown staking mode $STAKE_MODE. Check STAKE_MODE in env.sh "
        exit 1
esac

#=================================================================
# Prepare for elections
date +"---INFO: %F %T Current elections ID: $elections_id"

#=================================================
# Get Elections parametrs (p15)
echo "---INFO: Get elections parametrs (p15)"
if ! CONFIG_PAR_15="$(Get_NetConfig_P15)"; then
    result=$?
    case $result in
        1) echo "###-ERROR(${SelfScriptName} line $LINENO): Error get network election params (p15) from frontend by $CLI_BIN_NAME" ;;
        2) echo "###-ERROR(${SelfScriptName} line $LINENO): Error get network election params (p15) the node by console" ;;
        3) echo "###-ERROR(${SelfScriptName} line $LINENO): Election params (p15) is empty" ;;
    esac
    exit 1
else
    read -r validators_elected_for elections_start_before elections_end_before stake_held_for <<< "$CONFIG_PAR_15"
fi
Validating_Start=${elections_id}
Validating_Stop=$(( Validating_Start + 1000 + validators_elected_for + elections_start_before + elections_end_before + stake_held_for ))
echo "Validating_Start: $Validating_Start | Validating_Stop: $Validating_Stop"

next_election_id=$((elections_id + validators_elected_for))
if [[ "${Proxy_ID}" == "0" ]];then
    echo "1" > "${CURR_ELECTIONS_DIR}/${next_election_id}_proxy.id"
else
    echo "0" > "${CURR_ELECTIONS_DIR}/${next_election_id}_proxy.id"
fi

#=================================================
# Checking that query.boc already made for sending to Elector
if [[ -f ${CURR_ELECTIONS_DIR}/${elections_id}_query.boc ]];then
    echo "+++WARNING(${SelfScriptName} line $LINENO): ${elections_id}_query.boc for current elections generated already. We will use the existing one."
else
# Make query.boc to send to Elector
    # Check node supported block version if it is main network
    if [[ "${NETWORK_TYPE%%.*}" =~ ^(main|mainnet)$ ]];then
        Blk_vers="$(Get_Supported_Blocks_Version)"
        declare -i Net_Blk_Ver=$(echo "$Blk_vers"|awk '{print $1}')
        declare -i Git_Blk_Ver=$(echo "$Blk_vers"|awk '{print $2}')
        declare -i Nod_Blk_Ver=$(echo "$Blk_vers"|awk '{print $3}')
        echo "----INFO: Min allowed Block Version: $Node_Blk_Min_Ver. You have NetBV: $Net_Blk_Ver, GitBV: $Git_Blk_Ver, NodeBV: $Nod_Blk_Ver."
        if [[ $Node_Blk_Min_Ver -gt $Git_Blk_Ver ]] || [[ $Node_Blk_Min_Ver -gt $Nod_Blk_Ver ]];then
            echo -e "${BoldText}${RedBack}###-ALARM: Node version is TOO OLD. Your node can harm the network. Update node ASAP! Next update your part in elections will be disabled! ${NormText}"
            Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_SOS_sign ###-ALARM: Node version is TOO OLD. Your node can harm the network. Update node ASAP! Next update your part in elections will be disabled!" > /dev/null 2>&1
        fi
    fi
    case "$STAKE_MODE" in
        depool)
            jq ".wallet_id = \"${DP_Round_Proxy}\"" "${NODE_CFG_DIR}/console.json" > "${NODE_CFG_DIR}/console.tmp" ;;
        msig)
            jq ".wallet_id = \"${Validator_addr}\"" "${NODE_CFG_DIR}/console.json" > "${NODE_CFG_DIR}/console.tmp" ;;
        *)
            echo "###-ERROR(${SelfScriptName} line $LINENO): Unknown staking mode $STAKE_MODE. Check STAKE_MODE in env.sh "
            exit 1
    esac
    mv -f "${NODE_CFG_DIR}/console.tmp"  "${NODE_CFG_DIR}/console.json"
    if ! $CALL_CONS -c "election-bid $Validating_Start $Validating_Stop ${INPL_CURR_ELECTIONS_DIR}/${elections_id}_query.boc ${NET_GLOBAL_ID}" &> "${CURR_ELECTIONS_DIR}/${elections_id}-bid.log";then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot make query.boc for elections $elections_id. Can't continue. Exit"
        exit 1
    fi
fi

######################################################################################################
# prepare validator query to elector contract using multisig for lite-client
validator_query_payload=$(base64 "${CURR_ELECTIONS_DIR}/${elections_id}_query.boc" |tr -d "\n")

# ===============================================================
# Check that payload is not empty
if [[ -z $validator_query_payload ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Payload is empty! It is unasseptable!"
    echo "did you have right ${elections_id}_query.boc ?"
    exit 1
fi

# ===============================================================
# Calculate stake for BOC
declare -i NANOSTAKE
case "$STAKE_MODE" in
    msig)
        # Get stake parameters
        declare -r Stake_DST_Addr=$elector_addr
        declare -ir Initial_Tx_Qty=$Exist_El_Trans_Qty
        MSIG_FIX_STAKE=$((MSIG_FIX_STAKE))
        Validator_Acc_Balance=$(( Validator_Acc_Balance_nT / 1000000000 ))
        # ================================== 
        # Fixed stake for elections
        if [[ $MSIG_FIX_STAKE -gt 0 ]];then     
            if [[ $Validator_Acc_Balance -gt $MSIG_FIX_STAKE ]];then
                NANOSTAKE=$((MSIG_FIX_STAKE * 1000000000))
            else
                echo "###-ERROR(${SelfScriptName} line $LINENO): You do not have enough tokens in your account. You set stake $MSIG_FIX_STAKE but you have $Validator_Acc_Balance only."
                exit 1
            fi
        else
        # ================================== 
        # Stake for full balance devide to 2 rounds
            if [[ $Validator_Acc_Balance -gt $VAL_ACC_INIT_BAL ]];then    # first time staking for full balance
                NANOSTAKE=$(( (Validator_Acc_Balance / 2 - VAL_ACC_RESERVED) * 1000000000))
            else
                NANOSTAKE=$(( (Validator_Acc_Balance - VAL_ACC_RESERVED)  * 1000000000))
            fi
        fi
        echo "---INFO: You stake: $(printf "%'9.2f" "$(echo $((NANOSTAKE)) / 1000000000 | jq -nf /dev/stdin)") Tk / $NANOSTAKE nTk"
        ;;
    depool)
        NANOSTAKE=$((1 * 1000000000))
        declare -r Stake_DST_Addr=$Depool_addr
        declare -ir Initial_Tx_Qty=$Exist_DP_Trans_Qty
        ;;
    *)
        echo "###-ERROR(${SelfScriptName} line $LINENO): Unknown staking mode $STAKE_MODE. Check STAKE_MODE in env.sh "
        exit 1
esac

#####################################################################################################
###############  Send request to participate in elections ###########################################
#####################################################################################################
declare -i New_Trans_Qty=0
# ===============================================================
# make boc for sending
if ! LC_OUTPUT="$($CALL_CLI message --raw --output "${INPL_CURR_ELECTIONS_DIR}/${elections_id}_vaidator-query-msg.boc" \
    --sign "${INPL_KEYS_DIR}/${MSIG_KEY_FILES_ARRAY[0]##*/}" ${SIG_ID_OPTION} \
    --abi "$INPL_SafeC_Wallet_ABI" \
    "$Validator_addr" submitTransaction \
    "{\"dest\":\"$Stake_DST_Addr\",\"value\":$NANOSTAKE,\"bounce\":true,\"allBalance\":false,\"payload\":\"$validator_query_payload\"}")"
then
    echoerr "###-ERROR(${SelfScriptName} line $LINENO): CANNOT create message boc file for elections!!!"
fi
if [[ -f "${CURR_ELECTIONS_DIR}/${elections_id}_vaidator-query-msg.boc}" ]];then
    echoerr "###-ERROR(${SelfScriptName} line $LINENO): Cannot find ${CURR_ELECTIONS_DIR}/${elections_id}_vaidator-query-msg.boc file!"
fi
echo -e "\n-----------------------------------------------------------"
echo "message to Elector was created and signed by ${MSIG_KEY_FILES_ARRAY[0]}"
echo "$LC_OUTPUT"
echo -e "-----------------------------------------------------------\n"

#=================================================
# Send request to Elector
## 5x3 attempts to make trasaction
for (( TryToSetEl=0; TryToSetEl <= 5; TryToSetEl++ ));do
    echo -n "---INFO: Send query to Elector... "
    #################
    if ! New_Elect_Trans_ID=$(Send_Message "${Validator_addr}" "${Stake_DST_Addr}" "${INPL_CURR_ELECTIONS_DIR}/${elections_id}_vaidator-query-msg.boc");then
    #################
        echo "###-ERROR(${SelfScriptName} line $LINENO): ALARM!!! Cannot make transaction for elections!!!"
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
    New_Trans_Qty=$(( $(echo "$Trans_List" | jq -r "[.transactions[]|select(.dest == \"$Stake_DST_Addr\")]|length") ))
    if [[ $New_Trans_Qty -gt $Initial_Tx_Qty ]];then
        Elect_Trans_ID=$(echo "$Trans_List" | jq -r ".transactions[]|select(.dest == \"$Stake_DST_Addr\")|.id"|tail -n 1)
        if [[ $New_Elect_Trans_ID -ne $Elect_Trans_ID ]];then
            echo "###-WARNING(${SelfScriptName} line $LINENO): Transaction from Send_Message function ($New_Elect_Trans_ID) is not equal to last transaction from list ($Elect_Trans_ID)"
        fi
        echo "---INFO: Making transaction for elections was done SUCCESSFULLY! Trnasaction ID: $Elect_Trans_ID You have to sign this transaction!!"| tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        echo "Made transaction ID: $Elect_Trans_ID" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        
        # Sign transaction by all keys which we have
        # $LocalKeysQty  $Required_Signs
        for (( i=0; i < LocalKeysQty; i++ )); do
            echo "---INFO: Sign transaction for participate in elections $elections_id with ${MSIG_KEY_FILES_ARRAY[$i]}..."
            Send_MSIG_Trans_Confirmation "$Elect_Trans_ID" "$Validator_addr" "${MSIG_KEY_FILES_ARRAY[$i]##*/}"
            sleep $LC_Send_MSG_Timeout
        done
        Trans_List="$(Get_MSIG_Trans_List "${Validator_addr}")"
        if ! $RemoteSign;then
            # Check TransID is signed by all keys and send it to elector
            if [[ -z $(echo "$Trans_List" | jq -r ".transactions[]|select(.id == \"$Elect_Trans_ID\")") ]];then
                echo "+++-INFO: Transaction for elections was signed by all keys and sent successfully!"
            else
                echo "Signs required: $Required_Signs"
                echo "Signs made: $(echo "$Trans_List" | jq -r ".transactions[]|select(.id == \"$Elect_Trans_ID\")|.signsReceived")"
                echo "###-ERROR(${SelfScriptName} line $LINENO): Transaction for participate in elections $elections_id was NOT signed by all keys!!!"
            fi
        else
            echo "Signs required: $Required_Signs"
            echo "Signs made: $(echo "$Trans_List" | jq -r ".transactions[]|select(.id == \"$Elect_Trans_ID\")|.signsReceived")"
            echo "+++WARNING: You have NOT enough keys to sign transaction $Elect_Trans_ID locally, so you have to sign it remotely"
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
        echo "---INFO: Sending transaction for participate in elections $elections_id was done SUCCESSFULLY!"| tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log" 
    else
        echo "###-ERROR(${SelfScriptName} line $LINENO): Sending transaction for participate in elections $elections_id FAILED!!!" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        Send_msg_toTelBot "$VALIDATOR_NAME Server" \
            "$Tg_SOS_sign ###-ERROR(${SelfScriptName} line $LINENO): Sending transaction for for participate in elections $elections_id FAILED!!!" > /dev/null 2>&1
    fi
fi
echo
echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "================================================================================================"
exit 0
