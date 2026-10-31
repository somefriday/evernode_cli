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
    nominator)
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

if [[ "$STAKE_MODE" == "nominator" ]];then
    if [[ -z ${NOMINATOR_ADDR:-} ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Staking mode is nominator but NOMINATOR_ADDR is not set in env.sh."
        exit 1
    fi
    if [[ "${NOMINATOR_ADDR%%:*}" != "-1" ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): The nominator contract must be in the masterchain (-1:xx), not ${NOMINATOR_ADDR}."
        exit 1
    fi
fi

echo "---INFO: validator account address: $Validator_addr"
[[ "$STAKE_MODE" == "depool" ]] && echo "---INFO: depool   contract address: $Depool_addr"
[[ "$STAKE_MODE" == "nominator" ]] && echo "---INFO: nominator contract address: $NOMINATOR_ADDR"

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
# The node's own console is the only transport, so an unsynced or unreachable
# node refuses the attempt here. One check covers both chains and both ways of
# being unusable: the earlier masterchain test read Get_TimeDiff's error text
# ("Node Down") as a number, which bash evaluates to 0, so a dead node passed
# it; and shard lag was reported as a warning and then ignored, although a
# lagging shard is exactly the one that cannot show whether the bid landed.
Require_Node_Console "$TIMEDIFF_MAX" || exit 1
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
# Whether the elector's own participant list can be consulted. When it cannot,
# the bid still goes out and the verdict comes from what the money did.
Elector_Readable=true
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
    if ADNL_Found="$(Elector_ADNL_Search "$Next_ADNL_Key")";then
        read -r ADNL_Stake ADNL_Time ADNL_Max_Factor You_PubKey <<< "$ADNL_Found"
    else
        error_code=$?
        # Parse error code
        case $error_code in
            1) echo "###-ERROR(${SelfScriptName} line $LINENO): Wrong ADNL key: $Next_ADNL_Key." ;;
            2) echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot get Elector type!" ;;
            3) echo "###-ERROR(${SelfScriptName} line $LINENO): Elections is closed" ;;
            4) echo "---INFO: ADNL not found in Elector's participants list; preparing a new bid" ;;
            5) echo "###-ERROR(${SelfScriptName} line $LINENO): Unknown Elector type: $ELECTOR_TYPE" ;;
            6) echo "+++-WARNING(${SelfScriptName} line $LINENO): The elector's participant list cannot be read with any bundled interface description, so whether this node already bid has to be decided from what this node itself recorded." ;;
        esac
        # An elector this node cannot read must not stop it validating. The
        # question the list answers is only "did I already bid in this
        # election", and bidding twice would spend the stake twice - so it is
        # answered from the request file this node writes immediately before
        # it sends a bid. Erring towards "already bid" costs one round;
        # erring the other way costs principal, so the file is trusted.
        if [[ $error_code -eq 6 ]];then
            Elector_Readable=false
            if [[ -f ${CURR_ELECTIONS_DIR}/${elections_id}_query.boc ]];then
                echo "+++-WARNING(${SelfScriptName} line $LINENO): This node has a request on file for elections ${elections_id}, so it has already bid and will not bid again. Acceptance could not be confirmed; check the elector directly before assuming the round was missed."
                echo
                echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date +'%F %T %Z')"
                echo "================================================================================================"
                exit 0
            fi
            echo "---INFO: no request on file for elections ${elections_id}; bidding."
        elif [[ $error_code -ne 4 ]] || [[ $ADNL_Found != "absent" ]]; then
            exit 1
        fi
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
declare -i Exist_Nom_Trans_Qty=0
declare -i Exist_Proxy0_Trans_Qty=0
declare -i Exist_Proxy1_Trans_Qty=0
if [[ $Trans_QTY -gt 0 ]];then
    [[ "$STAKE_MODE" == "msig" ]]   && Exist_El_Trans_Qty=$(echo "$Trans_List" | jq -r "[.transactions[]|select(.dest == \"$elector_addr\")]|length")
    [[ "$STAKE_MODE" == "nominator" ]] && Exist_Nom_Trans_Qty=$(echo "$Trans_List" | jq -r "[.transactions[]|select(.dest == \"$NOMINATOR_ADDR\")]|length")
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
    nominator)
        if [[ $Exist_Nom_Trans_Qty -gt 0 ]];then
            echo "###-ERROR(${SelfScriptName} line $LINENO): You have unsigned transactions to the nominator contract. Wait until they will be confirmed."
            Send_msg_toTelBot "$VALIDATOR_NAME Server" \
                "${Tg_Warn_sign} WARNING($SelfScriptName line $LINENO): You have unsigned transactions to the nominator contract. Wait until they will be confirmed." > /dev/null 2>&1
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
        nominator)
            jq ".wallet_id = \"${NOMINATOR_ADDR}\"" "${NODE_CFG_DIR}/console.json" > "${NODE_CFG_DIR}/console.tmp" ;;
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
    nominator)
        # The contract holds the principal and takes the stake from its own
        # balance, so the wallet attaches only what the instruction costs and
        # the amount to stake travels inside the payload. That is the same
        # shape as depool mode, not solo mode.
        if [[ $MSIG_FIX_STAKE -le 0 ]];then
            echo "###-ERROR(${SelfScriptName} line $LINENO): MSIG_FIX_STAKE is not set, so there is no stake to bid with. Run: evernode stake set"
            exit 1
        fi
        declare -i Nominator_Balance_nT=0
        if ! result=$(Get_Account_Info "${NOMINATOR_ADDR}");then
            echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot read the nominator contract account ${NOMINATOR_ADDR}."
            exit 1
        fi
        read -r Nominator_Acc_Status Nominator_Balance_nT _ <<< "$result"
        if [[ "$Nominator_Acc_Status" != "Active" ]];then
            echo "###-ERROR(${SelfScriptName} line $LINENO): The nominator contract ${NOMINATOR_ADDR} is ${Nominator_Acc_Status}, not Active. Nothing can be staked from it."
            exit 1
        fi
        # The contract refuses a stake that would take it below its storage
        # reserve, so check that here rather than letting it throw 0x2004.
        declare -i Nominator_Reserve_nT=$(( ${NOMINATOR_RESERVE:-1000000000} ))
        declare -i Nominator_Stake_nT=$(( MSIG_FIX_STAKE * 1000000000 ))
        if [[ $(( Nominator_Stake_nT + Nominator_Reserve_nT )) -gt $Nominator_Balance_nT ]];then
            echo "###-ERROR(${SelfScriptName} line $LINENO): The nominator contract holds $(( Nominator_Balance_nT / 1000000000 )) tokens, which does not cover a stake of ${MSIG_FIX_STAKE} plus its $(( Nominator_Reserve_nT / 1000000000 )) token reserve. A stake still frozen at the elector cannot be used again until its round retires."
            exit 1
        fi
        declare -i Nominator_Fee_nT=$(( ${NOMINATOR_OP_FEE:-2000000000} ))
        if [[ $Validator_Acc_Balance_nT -lt $(( Nominator_Fee_nT + 1000000000 )) ]];then
            echo "###-ERROR(${SelfScriptName} line $LINENO): The wallet holds $(( Validator_Acc_Balance_nT / 1000000000 )) tokens, which is not enough gas to instruct the contract. Top it up; the principal stays in the contract."
            exit 1
        fi
        # The contract expects the elector's own request with the amount it
        # should take inserted after the query id, and forwards the rest
        # untouched. Only the cell tool can splice that, so it runs in the
        # pinned image the contract was compiled with.
        if [[ -z ${NOMINATOR_TOOLS_IMAGE:-} ]];then
            echo "###-ERROR(${SelfScriptName} line $LINENO): NOMINATOR_TOOLS_IMAGE is not set in env.sh, so the election request cannot be prepared. Run: evernode nominator prepare"
            exit 1
        fi
        echo -n "---INFO: Insert the stake of ${MSIG_FIX_STAKE} tokens into the election request ... "
        if ! docker run --rm --network none -v "${CURR_ELECTIONS_DIR}:/work" \
                -w /opt/nominator "$NOMINATOR_TOOLS_IMAGE" \
                "node tools/cells.cjs body stake /work/${elections_id}_stake.boc /work/${elections_id}_query.boc ${Nominator_Stake_nT}" >/dev/null;then
            echo
            echo "###-ERROR(${SelfScriptName} line $LINENO): Could not build the nominator election request from ${elections_id}_query.boc."
            exit 1
        fi
        echo "DONE"
        validator_query_payload=$(base64 "${CURR_ELECTIONS_DIR}/${elections_id}_stake.boc" | tr -d "\n")
        if [[ -z $validator_query_payload ]];then
            echo "###-ERROR(${SelfScriptName} line $LINENO): The nominator election request is empty! It is unasseptable!"
            exit 1
        fi
        echo "---INFO: The contract stakes: $(printf "%'9.2f" "$(echo $((Nominator_Stake_nT)) / 1000000000 | jq -nf /dev/stdin)") Tk / $Nominator_Stake_nT nTk"
        NANOSTAKE=$Nominator_Fee_nT
        declare -r Stake_DST_Addr=$NOMINATOR_ADDR
        declare -ir Initial_Tx_Qty=$Exist_Nom_Trans_Qty
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
# Sampled before anything is sent, so an unreadable elector can still be
# answered by what the money did. Read for every mode, used only when needed.
declare -i Staking_Balance_Before=0
case "$STAKE_MODE" in
    msig)      Staking_Balance_Before=$Validator_Acc_Balance_nT ;;
    nominator) Staking_Balance_Before=$Nominator_Balance_nT ;;
esac
declare -i New_Trans_Qty=0
# ===============================================================
# make boc for sending
if ! LC_OUTPUT="$($CALL_CLI message --raw --output "${INPL_CURR_ELECTIONS_DIR}/${elections_id}_vaidator-query-msg.boc" \
    --lifetime "$MSG_LIFETIME" \
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
# Send the request to the Elector
#=================================================
# A retry must finish the bid an earlier attempt already submitted, never add
# a second one. Submission and confirmation are separate on-chain messages, so
# an attempt that died between them left a real transaction holding one
# signature; submitting again would put another half-signed bid in the queue,
# and five of those from one custodian fill it and stop the node bidding at
# all. The six DePool operations have resumed like this for a while; this path
# was the one that still did not.
New_Elect_Trans_ID=""
if [[ ${Required_Signs} -gt 1 ]];then
    New_Elect_Trans_ID=$(Find_Matching_Pending_Trans "$Validator_addr" "$Stake_DST_Addr" \
        "$NANOSTAKE" "$validator_query_payload" "${MSIG_KEY_FILES_ARRAY[0]}") || exit 1
    if [[ -n $New_Elect_Trans_ID ]];then
        echo "---INFO: Resuming the matching bid $New_Elect_Trans_ID already in the wallet."
    fi
fi
if [[ -z $New_Elect_Trans_ID && ${Required_Signs} -gt 1 ]];then
    # A sixth live transaction from this custodian would be rejected with 113
    # before tvm.accept(), leaving nothing on chain to explain itself.
    Require_Queue_Space "$Validator_addr" "${MSIG_KEY_FILES_ARRAY[0]}" || exit 1
fi
if [[ -z $New_Elect_Trans_ID ]];then
    # Send_Message counted the pending entries addressed to the destination and
    # returned the newest by position, so a transfer another custodian had just
    # submitted to the same address could be the one it handed back - and the
    # signatures below would then have gone to someone else's transaction.
    if ! New_Elect_Trans_ID=$(Submit_And_Identify "${Validator_addr}" "${Stake_DST_Addr}" \
            "${INPL_CURR_ELECTIONS_DIR}/${elections_id}_vaidator-query-msg.boc" \
            "$NANOSTAKE" "$validator_query_payload" "${MSIG_KEY_FILES_ARRAY[0]}");then
        echo "###-ERROR(${SelfScriptName} line $LINENO): The bid for elections $elections_id was not accepted by the wallet, so nothing was signed and nothing was staked." \
            | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        exit 1
    fi
    echo "+++-INFO: Made transaction ID: $New_Elect_Trans_ID"
fi

#=================================================
# Final checking: wait until the elector lists this node as a participant.
#=================================================
# Nothing short of that says the bid worked. Reading the wallet's pending list
# cannot: the wallet checks every require() before tvm.accept(), so a bid it
# rejected is recorded in no block at all, and an entry that has gone may have
# executed, expired, or never existed. A single read of that list also loses
# the race with inclusion, which is how a bid that did reach the DePool came to
# be reported as never signed.
#
# The deadline is when the elections close. A bid that registers after that
# cannot be elected, so waiting the wallet's full hour would turn a clean
# failure into a missed round, and waiting less would call a slow success a
# failure.
declare -i Elections_Close=$(( elections_id - elections_end_before ))
# Waiting is useful at least until every message this attempt sent is dead,
# whatever the election calendar says. A network that has stalled keeps its
# election open long past this nominal close - the local test network sat four
# hours past it with the set unfilled - and the elector still accepts bids
# there, which is how such a network recovers. Reporting a failure the instant
# after submitting, because a date in the past said so, would be Fault A again
# with a different cause: the bid in flight would land seconds later.
declare -i Bid_Deadline=$(( $(date +%s) + 2 * ${MSG_LIFETIME:-60} ))
if [[ $Elections_Close -gt $Bid_Deadline ]];then
    Bid_Deadline=$Elections_Close
fi
Elect_Trans_ID="$New_Elect_Trans_ID"
if [[ $Required_Signs -gt 1 ]];then
    echo "---INFO: Made transaction ID: $Elect_Trans_ID" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
    # One signature at a time, each one waited for before the next is sent, and
    # each key taken from the wallet's own confirmation mask rather than
    # guessed from a count. Sending them all and reading the result once is
    # what reported a bid that had reached the chain as unsigned.
    declare -i Sign_Deadline=$Bid_Deadline
    Bid_Expires_At="$(Trans_Expires_At "$Validator_addr" "$Elect_Trans_ID")"
    if [[ $Bid_Expires_At =~ ^[0-9]+$ ]] && [[ $((Bid_Expires_At - 5)) -lt $Sign_Deadline ]];then
        Sign_Deadline=$((Bid_Expires_At - 5))
    fi
    Sign_To_Execution "$Validator_addr" "$VALIDATOR_NAME" "$Elect_Trans_ID" "$Sign_Deadline" \
        "The bid in elections $elections_id"
    Sign_Status=$?
    if [[ $Sign_Status -eq 3 ]] && $RemoteSign;then
        echo "+++WARNING: This node holds $LocalKeysQty of the $Required_Signs keys this wallet requires, so transaction $Elect_Trans_ID has to be signed elsewhere before the elections close at $(TD_unix2human "$Elections_Close")." \
            | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        exit 0
    fi
    if [[ $Sign_Status -ne 0 ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): The bid for elections $elections_id could not be signed through to execution." \
            | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        exit 1
    fi
fi
# The ADNL this bid registers under is the key console generated for it, so it
# is read now rather than reused from the participation check above, which ran
# before the request existed.
if ! Bid_ADNL=$(Get_Engine_ADNL_For_Election "$elections_id");then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot find this node's validator key for elections $elections_id in ${NODE_CFG_DIR}/config.json, so the bid cannot be verified." \
        | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
    exit 1
fi
# When the elector's list cannot be read, the registration itself cannot be
# confirmed - but the money can. The stake leaves the staking account only if
# the elector accepted it: a refused stake is returned straight away. So the
# verdict becomes "it left and stayed gone", which is weaker evidence than the
# participant list and far stronger than nothing. A bid that moved funds
# correctly must never be reported as a failure.
if ! $Elector_Readable;then
    Staking_Account=""
    case "$STAKE_MODE" in
        msig)      Staking_Account="$Validator_addr" ;;
        nominator) Staking_Account="$NOMINATOR_ADDR" ;;
    esac
    if [[ -z $Staking_Account ]];then
        # A DePool stakes from its own rounds, so no single account's balance
        # answers this. Report the submission and what was not checked.
        echo "+++-WARNING: The bid in elections $elections_id was submitted, but the elector's participant list cannot be read with any bundled interface description, so its acceptance was not confirmed. Check the elector before assuming the round was won or missed." \
            | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        echo
        echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
        echo "================================================================================================"
        exit 0
    fi
    declare -i Stake_Gone_Below=$(( Staking_Balance_Before - NANOSTAKE + 2000000000 ))
    if Await_Effect "The stake leaving ${Staking_Account}" "$Bid_Deadline" \
            "$LC_Send_MSG_Timeout" Effect_Account_Balance_At_Most \
            "$Staking_Account" "$Stake_Gone_Below";then
        echo "+++-INFO: The stake of $(( NANOSTAKE / 1000000000 )) tokens left ${Staking_Account} and was not returned, so the elector accepted the bid in elections $elections_id. Its participant list could not be read to confirm the registration itself." \
            | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
        echo
        echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
        echo "================================================================================================"
        exit 0
    fi
    echo "###-ERROR(${SelfScriptName} line $LINENO): The stake for elections $elections_id is still in ${Staking_Account}, so the elector did not take it: either the request never arrived or it was refused and returned. The elector's participant list could not be read to say which." \
        | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" \
        "$Tg_SOS_sign ###-ERROR: The stake for elections $elections_id did not leave the staking account." > /dev/null 2>&1
    echo
    echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
    echo "================================================================================================"
    exit 1
fi
if Await_Effect "The bid in elections $elections_id" "$Bid_Deadline" \
        "$LC_Send_MSG_Timeout" Effect_Elector_Has_Our_ADNL "$Bid_ADNL";then
    echo "+++-INFO: The elector lists this node as a participant of elections $elections_id." \
        | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
    echo
    echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
    echo "================================================================================================"
    exit 0
fi
# Say why, when the sending contract itself can say it. A round elected with no
# stake cannot produce a bid however well the wallet transaction was signed, so
# a retry cannot help; the operator needs to know where the stake actually is.
if [[ "$STAKE_MODE" == "nominator" ]];then
    # The elector returns a rejected stake to the contract and keeps no record
    # of it, so a bid that was refused looks exactly like one never made. The
    # contract's balance is what distinguishes them.
    Nominator_Balance_After_nT=0
    if result=$(Get_Account_Info "${NOMINATOR_ADDR}" 2>/dev/null);then
        read -r _ Nominator_Balance_After_nT _ <<< "$result"
    fi
    echo "###-ERROR(${SelfScriptName} line $LINENO): The bid in elections $elections_id is not registered with the elector. The nominator contract ${NOMINATOR_ADDR} now holds $(( Nominator_Balance_After_nT / 1000000000 )) tokens. If that still includes the stake, the elector refused the request and returned it: compare the configured stake against the elector's minimum and the stake already frozen for an earlier round. If it does not, the stake is at the elector and only the registration failed." \
        | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" \
        "$Tg_SOS_sign ###-ERROR(${SelfScriptName} line $LINENO): The bid in elections $elections_id is not registered with the elector." > /dev/null 2>&1
    echo
    echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
    echo "================================================================================================"
    exit 1
fi
Round_Stake="$(Get_DP_Round_Stake_For_Election "$Stake_DST_Addr" "$elections_id" 2>/dev/null)"
if [[ $Round_Stake =~ ^0+$ ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): The bid in elections $elections_id is not registered with the elector: the DePool holds no stake in the round being elected, so it had nothing to bid with. This is not a failure of this attempt and a retry cannot change it. A stake bids in the round that was collecting when it was made, and is then locked until that round completes, so the rounds collecting after it are empty until it returns. To bid in consecutive elections, add a stake to each round while it is the collecting one, at least as large as the validator assurance. Read 'evernode depool info' for which round is collecting and what each round holds; staking now does not move a locked stake into the round being elected." \
        | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
else
    echo "###-ERROR(${SelfScriptName} line $LINENO): The bid in elections $elections_id is not registered with the elector and the elections have closed.${Round_Stake:+ The DePool holds $Round_Stake nanotokens in that round.}" \
        | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}.log"
fi
Send_msg_toTelBot "$VALIDATOR_NAME Server" \
    "$Tg_SOS_sign ###-ERROR(${SelfScriptName} line $LINENO): The bid in elections $elections_id is not registered with the elector." > /dev/null 2>&1
echo
echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "================================================================================================"
exit 1
