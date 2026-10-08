#!/usr/bin/env bash
# shellcheck source=env.sh
# shellcheck source=functions.shinc
# shellcheck disable=SC2155

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
echo "######################### Set crontab for validation and autoupdate ############################"
SelfScriptName=$(basename "$0")
echo "--- INFO: ${SelfScriptName} BEGIN $(date +%s) / $(date  +'%F %T %Z')"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
if ! source "${SCRIPT_DIR}/env.sh"; then
    echo "###-ERROR: Can't load env.sh"
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
umask 000

SCRPT_USER=$USER
USER_HOME=$HOME
[[ -z "$SCRPT_USER" ]] && SCRPT_USER=$LOGNAME
if echo "$USER_HOME" |grep -q 'root';then SCRPT_USER="root"; fi
CALL_CRONTAB="sudo crontab"
[[ $SCRPT_USER == "root" ]] && CALL_CRONTAB="crontab"

#=================================================
# Get node sync status
NODE_SYNC_STATUS=$(Get_TimeDiff)
# Get time difference between the current machine and the node
# shellcheck disable=SC2046
read -r MC_TIME_DIFF SH_TIME_DIFF <<< $(echo "$NODE_SYNC_STATUS" | awk '{print $1, $2}')
echo "---INFO(${SelfScriptName} line $LINENO): Node sync status: $MC_TIME_DIFF $SH_TIME_DIFF"
if ! [[ $MC_TIME_DIFF =~ ^[0-9]+$ && $SH_TIME_DIFF =~ ^[0-9]+$ ]]; then
    # If one of the values is not a number, we assume that the node is not synchronized
    MC_TIME_DIFF=888
    SH_TIME_DIFF=888
    echo "---INFO: Modified sync status: $MC_TIME_DIFF $SH_TIME_DIFF"
fi
# if the time difference is more than 10 seconds, or the time difference is not defined, then set the script to check the synchronization in 5 minutes
if [[ -z $MC_TIME_DIFF || -z $SH_TIME_DIFF || $MC_TIME_DIFF -gt 10 || $SH_TIME_DIFF -gt 10 ]]; then
    echo "###-WARNING(${SelfScriptName}: line $LINENO): Node is not synchronized. Setting crontab to retry in 5 minutes."
    CRONT_JOBS="SHELL=/bin/bash
PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin:$NODE_BIN_DIR
HOME=$USER_HOME
*/5 * * * *    cd ${SCRIPT_DIR} && ./crontab_setup.sh &>> ${NODE_LOG_DIR}/crontab.log"
    echo "$CRONT_JOBS" | $CALL_CRONTAB -u "$SCRPT_USER" -
    exit 0
else
    echo "---INFO(${SelfScriptName}: line $LINENO): Node is synchronized. Continue setting crontab."
fi

#=================================================
# Print environment info
echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo

OS_SYSTEM=$(uname -s); export OS_SYSTEM
# ===================================================
# Convert unixtime to crontab format
function GET_M_H() {
    ival="${1}"
    if [[ "$OS_SYSTEM" == "Linux" ]];then
        date  +'%M %H' -d "@$ival"
    else
        date -r "$ival" +'%M %H'
    fi
}
function GET_M_H_D() {
    ival="${1}"
    if [[ "$OS_SYSTEM" == "Linux" ]];then
        date  +'%M %H %d' -d "@$ival"
    else
        date -r "$ival" +'%M %H %d'
    fi
}

#######################################################################################################
#===================================================
# Get current electoin cycle info
election_id=$(Get_Current_Elections_ID)
echo "---INFO(${SelfScriptName} line $LINENO): Current Election ID: $election_id"

if $FORCE_USE_DAPP ;then
    ELECT_TIME_PAR=$($CALL_CLI -j getconfig 15 | jq)
    LIST_CURR_VALS=$($CALL_CLI -j getconfig 34 | jq)
    LIST_NEXT_VALS=$($CALL_CLI -j getconfig 36 | jq)
else
    ELECT_TIME_PAR=$($CALL_CONS -j -c "getconfig 15" | jq .p15)
    LIST_CURR_VALS=$($CALL_CONS -j -c "getconfig 34" | jq .p34)
    LIST_NEXT_VALS=$($CALL_CONS -j -c "getconfig 36" | jq .p36)
fi
declare -i CURR_VAL_UNTIL=$(echo "${LIST_CURR_VALS}" | jq -r '.utime_until')	        # utime_until
if [[ "$election_id" == "0" ]];then 
    CURR_VAL_UNTIL=$(echo "${LIST_CURR_VALS}" | jq -r '.utime_since')	                # utime_unti
    if [[ "$(echo "${LIST_NEXT_VALS}"|head -n 1)" != 'null' ]];then
        CURR_VAL_UNTIL=$(echo "${LIST_NEXT_VALS}" | jq -r '.utime_since')	            # utime_sinc
    fi
fi
declare -i VAL_DUR=$(echo "${ELECT_TIME_PAR}"        | jq -r '.validators_elected_for')	# validators_elected_for
declare -i STRT_BEFORE=$(echo "${ELECT_TIME_PAR}"    | jq -r '.elections_start_before')	# elections_start_before

#===================================================
# Calculate previous election time
PREV_ELECTION_TIME=$((CURR_VAL_UNTIL - STRT_BEFORE + TIME_SHIFT + DELAY_TIME))
PREV_ELECTION_SECOND_TIME=$((PREV_ELECTION_TIME + TIME_SHIFT))
PREV_ADNL_TIME=$((PREV_ELECTION_SECOND_TIME + TIME_SHIFT))

#===================================================
# Calculate update time based on validator address
declare -i Validator_Upd_Ord=128        # 0xFF/2 - means middle of the election cycle
# if validator address is found, set update time based on it 0x{33,34} bytes from address (0x00 - 0xFF)
if Validator_addr="$(NameToAddr "$VALIDATOR_NAME")"; then
    declare -i Validator_Upd_Ord=$(( $(hex2dec "$(echo "$Validator_addr"|cut -c 33,34)") ))
fi
declare -i Upd_Interval=$(( VAL_DUR / 256 / 60 * 60 ))
if [[ $Upd_Interval -le 0 ]];then
    Upd_Interval=$(( VAL_DUR / 128 / 60 * 60 ))
    Validator_Upd_Ord=$((  Validator_Upd_Ord / 2 ))
fi
NEXT_UPD_TIME=$((PREV_ADNL_TIME + Validator_Upd_Ord * Upd_Interval))

#===================================================
# Convert time to crontab format
NODE_UPDATE_TIME="$(GET_M_H "$NEXT_UPD_TIME") *"

#===================================================
# Get last node update info
UpdateByCron=true
LINC_present=false
if ! LNI_Info="$( get_LastNodeInfo )";then
    result=$?
    case $result in
        1) echo "###-WARNING(line $LINENO): LNIC_ADDRESS is empty!" ;;
        2) echo "###-WARNING(line $LINENO): LNIC account not found." ;;
        3) echo "###-WARNING(line $LINENO): Error getting LNIC state." ;;
        4) echo "###-WARNING(line $LINENO): Failed to decode LNIC ABI from HEX to binary" ;;
        5) echo "###-WARNING(line $LINENO): Failed to decompress LNIC ABI" ;;
        6) echo "###-WARNING(line $LINENO): Unknown compression format in LNIC ABI." ;;
        7) echo "###-WARNING(line $LINENO): Cannot get LNIC ABI from contract boc." ;;
        8) echo "###-WARNING(line $LINENO): Last node info from contract is empty." ;;
        *) echo "###-WARNING(line $LINENO): Unknown error." ;;
    esac
    echo "###-WARNING(line $LINENO): Last node info from contract not found."
else
    export LINC_present=true
    declare -i UpdateStartTime=$(echo "$LNI_Info" | jq -r '.UpdateStartTime')
    declare -i UpdateDuration=$(echo "$LNI_Info" | jq -r '.UpdateDuration')
    UpdateByCron=$(echo "$LNI_Info" | jq -r '.UpdateByCron')
fi

if $LINC_present && [[ $UpdateStartTime -gt 0 ]] && [[ $UpdateDuration -gt 0 ]];then
    #=================================================
    # Calculate time to update
    declare -i UpdateStartTime=$(echo "$LNI_Info" | jq -r '.UpdateStartTime')
    declare -i UpdateDuration=$(echo "$LNI_Info" | jq -r '.UpdateDuration')
    
    declare -i UpdTimeShift=$(( UpdateDuration * Validator_Upd_Ord / 256))
    declare -i CurrNodeUpdTime=$(( UpdTimeShift + UpdateStartTime))
    
    #--------------------------------------------------
    Prep_Elect_Time=$((CURR_VAL_UNTIL - STRT_BEFORE + DELAY_TIME))
    #--------------------------------------------------
    
    ElectionsSkip=$(( (CurrNodeUpdTime - Prep_Elect_Time) / VAL_DUR ))
    NearElectionsID=$((Prep_Elect_Time + (ElectionsSkip * VAL_DUR) ))
    TimeRest=$(( CurrNodeUpdTime - NearElectionsID ))
    
    [[ $TimeRest -lt 1800 ]] && CurrNodeUpdTime=$(( CurrNodeUpdTime + (1800 - TimeRest) ))
    [[ $TimeRest -gt $((VAL_DUR - 1800)) ]] && CurrNodeUpdTime=$(( CurrNodeUpdTime - (1800 - (VAL_DUR - TimeRest) ) ))
    # String for cron
    NODE_UPDATE_TIME="$(GET_M_H_D $CurrNodeUpdTime)"
    declare -i CurrTime=$(date +%s)
    [[ $CurrTime -gt $(( CurrNodeUpdTime + VAL_DUR )) ]] && NODE_UPDATE_TIME="$(GET_M_H "$NEXT_UPD_TIME") *"
fi

# If UpdateByCron in LNIC is false (which means autoupdate is disabled), comment out the line with the update time.
if ! $UpdateByCron;then
    NODE_UPDATE_TIME="# $NODE_UPDATE_TIME"
fi

################################################################################################
# Set crontab

Curr_Elect_Time=$((CURR_VAL_UNTIL - STRT_BEFORE))
Next_Elect_Time=$((CURR_VAL_UNTIL + VAL_DUR - STRT_BEFORE))
echo
echo "Current elections time start: $Curr_Elect_Time / $(TD_unix2human "$Curr_Elect_Time")"
echo "Next elections time start: $Next_Elect_Time / $(TD_unix2human "$Next_Elect_Time")"
echo "-------------------------------------------------------------------"

#===================================================
# Set sync node alarm to telegram bot if token is present
TlgStartAtReboot=""
if [[ -n $TELEGRAM_BOT_TOKEN ]];then
    TlgStartAtReboot="@reboot cd ${SCRIPT_DIR} && tmux new -ds tg && sleep 3 && tmux send -t tg.0 './tg_check_node_sync_status.sh &' ENTER"
fi

#===================================================
# Prepare crontab for FreeBSD
if [[ "$OS_SYSTEM" == "FreeBSD" ]];then

CRONT_JOBS=$(cat <<-_ENDCRN_
SHELL=/bin/bash
PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin:$NODE_BIN_DIR
HOME=$USER_HOME
$TlgStartAtReboot
@reboot cd ${SCRIPT_DIR} && ./wait_for_sync.sh && ./prepare_elections.sh; ./take_part_in_elections.sh; ./part_check.sh; ./crontab_setup.sh
*/${CRONTAB_INTERVAL} * * * * 	cd ${SCRIPT_DIR} && ./prepare_elections.sh >> ${VALIDATOR_LOG_DIR}/${VALIDATOR_LOG_FILE_NAME} 2>&1 && sleep 120 && ./take_part_in_elections.sh >> ${VALIDATOR_LOG_DIR}/${VALIDATOR_LOG_FILE_NAME} 2>&1 && sleep 120 && ./part_check.sh >> ${VALIDATOR_LOG_DIR}/${VALIDATOR_LOG_FILE_NAME} 2>&1
# $NODE_UPDATE_TIME * *    cd ${SCRIPT_DIR} && ./Update_ALL.sh &>> ${NODE_LOGS_ARCH}/NodeUpdate.log
_ENDCRN_
)

else
#===================================================
# Prepare crontab for Linux
CRONT_JOBS=$(cat <<-_ENDCRN_
SHELL=/bin/bash
PATH=$NODE_BIN_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
HOME=$USER_HOME
$TlgStartAtReboot
@reboot cd ${SCRIPT_DIR} && ./wait_for_sync.sh && ./prepare_elections.sh; ./take_part_in_elections.sh; ./part_check.sh; ./crontab_setup.sh
*/${CRONTAB_INTERVAL} * * * * 	cd ${SCRIPT_DIR} && ./prepare_elections.sh >> ${VALIDATOR_LOG_DIR}/${VALIDATOR_LOG_FILE_NAME} 2>&1 && sleep 120 && ./take_part_in_elections.sh >> ${VALIDATOR_LOG_DIR}/${VALIDATOR_LOG_FILE_NAME} 2>&1 && sleep 120 && ./part_check.sh >> ${VALIDATOR_LOG_DIR}/${VALIDATOR_LOG_FILE_NAME} 2>&1
# $NODE_UPDATE_TIME * *    cd ${SCRIPT_DIR} && ./Update_ALL.sh &>> ${NODE_LOGS_ARCH}/NodeUpdate.log
_ENDCRN_
)
fi

#===================================================
# Write crontab and show result

# just show what will be set and exit
[[ "$1" == "show" ]] && echo "$CRONT_JOBS"&& exit 0
# set crontab
echo "$CRONT_JOBS" | $CALL_CRONTAB -u "$SCRPT_USER" -
# show current crontab
$CALL_CRONTAB -l -u "$SCRPT_USER" | tail -n 8

echo "-------------------------------------------------------------------"

echo "+++INFO: ${SelfScriptName} FINISHED $(date +%s) / $(date)"
echo "================================================================================================"

exit 0
