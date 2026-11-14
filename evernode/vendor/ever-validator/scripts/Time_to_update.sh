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

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/functions.shinc"
OS_SYSTEM=$(uname -s)

# ===================================================
function GET_M_H_D() {
    ival="${1}"
    if [[ "$OS_SYSTEM" == "Linux" ]];then
        date  +'%M %H %d' -d @$ival
    else
        date -r $ival +'%M %H %d'
    fi
}

#=================================================
# Get LNIC ABI from contract
# LastNodeInfo.abi.json
LNIC_ADDRESS="0:bdcefecaae5d07d926f1fa881ea5b61d81ea748bd02136c0dbe76604323fc347"
GetABI='{"ABI version":2,"version":"2.2","header":["time","expire"],"functions":[{"name":"getABI","inputs":[],"outputs":[{"name":"ABI_7z_hex","type":"string"}]},{"name":"ABI","inputs":[],"outputs":[{"name":"ABI_7z_hex","type":"string"}]}],"data":[],"events":[],"fields":[{"name":"ABI_7z_hex","type":"string"}]}'
echo "$GetABI" > Get_ABI.json

$CALL_CLI account $LNIC_ADDRESS --dumpboc ${LNIC_ADDRESS##*:}.boc > /dev/null 2>&1
$CALL_CLI -j run --boc ${LNIC_ADDRESS##*:}.boc --abi Get_ABI.json ABI {} | jq -r '.ABI_7z_hex' > LNIC_ABI_7z_hex.txt
xxd -r -p LNIC_ABI_7z_hex.txt > LNIC_ABI.7z
$CALL_7Z x -y LNIC_ABI.7z > /dev/null 2>&1

ABI="LastNodeInfo.abi.json"
if [[ ! -e "${ABI}" ]];then
    echo "###-ERROR(line $LINENO): Cannot get LNIC ABI from state. Can't continue. Sorry."
    exit 1
fi

#=================================================
# Get Last node info from saved boc
LNI_Info="$($CALL_CLI -j run --boc ${LNIC_ADDRESS##*:}.boc --abi ${ABI} node_info {} | jq '.node_info')"

echo "${LNI_Info}"

rm -f ${LNIC_ADDRESS##*:}.boc Get_ABI.json LNIC_ABI_7z_hex.txt LNIC_ABI.7z LastNodeInfo.abi.json

#=================================================
# Current node info
Supp_Blocks="$(Get_Supported_Blocks_Version)"
Node_remote_commit="$(git --git-dir="${NODE_SRC_DIR}/.git" ls-remote 2>/dev/null | grep 'HEAD'|awk '{print $1}')"
Node_local_commit="$(git --git-dir="${NODE_SRC_DIR}/.git" rev-parse HEAD 2>/dev/null)"
Node_bin_commit="$("${CALL_NODE}" -V | grep 'NODE git commit' | awk '{print $5}')"
echo "-------------------------------------------------------------------------------------------"
echo "Node remote MASTER commit: $Node_remote_commit"
echo "Node local commit:         $Node_local_commit"
echo "Node commit in BINARY:     $Node_bin_commit"
echo "Net supported blocks:     $(echo $Supp_Blocks|awk '{print $1}')"
echo "Current node blocks:      $(echo $Supp_Blocks|awk '{print $2}')"
echo "Git master branch blocks: $(echo $Supp_Blocks|awk '{print $3}')"
echo "Node version in running service: $(echo $Supp_Blocks|awk '{print $4}')"
echo "-------------------------------------------------------------------------------------------"
#=================================================
# Node info from contract
LNIC_commit=$(echo ${LNI_Info} | jq -r '.LastCommit')
LNIC_Console_commit=$(echo ${LNI_Info} | jq -r '.ConsoleCommit')
LNIC_Node_Ver=$(echo ${LNI_Info} | jq -r '.NodeVersion')

echo "Node LNIC commit:          $LNIC_commit"
echo "LNIC node version:         $LNIC_Node_Ver"
echo "LNIC supported blocks:    $(echo ${LNI_Info}|jq -r '.SupportedBlock')"
echo "Console LNIC commit:       $LNIC_Console_commit"
echo "-------------------------------------------------------------------------------------------"

#=================================================
# Calculate node number in update queue
Validator_addr=`cat ${KEYS_DIR}/${VALIDATOR_NAME}.addr`
declare -i Validator_Upd_Ord=$(( $(hex2dec "$(echo $Validator_addr|cut -c 33,34)") ))
echo
echo "This node queue number: $Validator_Upd_Ord"
#=================================================
# Calculate time to update
declare -i UpdateStartTime=$(echo "$LNI_Info" | jq -r '.UpdateStartTime')
declare -i UpdateDuration=$(echo "$LNI_Info" | jq -r '.UpdateDuration')

declare -i UpdTimeShift=$(( UpdateDuration / 256 * Validator_Upd_Ord))
declare -i CurrNodeUpdTime=$(( UpdTimeShift + UpdateStartTime))

#--------------------------------------------------
election_id=$(Get_Current_Elections_ID)
ELECT_TIME_PAR=$($CALL_CLI -j getconfig 15)
declare -i VAL_DUR=`echo "${ELECT_TIME_PAR}"        | jq -r '.validators_elected_for'`
declare -i STRT_BEFORE=`echo "${ELECT_TIME_PAR}"    | jq -r '.elections_start_before'`
Curr_Elect_Time=$((election_id - STRT_BEFORE + DELAY_TIME))
#--------------------------------------------------

ElectionsSkip=$(( (CurrNodeUpdTime - Curr_Elect_Time) / VAL_DUR ))
NearElectionsID=$((Curr_Elect_Time + (ElectionsSkip * VAL_DUR) ))
NextElectionsID=$((Curr_Elect_Time + ((ElectionsSkip + 1) * VAL_DUR) ))
TimeRest=$(( CurrNodeUpdTime - NearElectionsID ))

[[ $TimeRest -lt 1800 ]] && CurrNodeUpdTime=$(( CurrNodeUpdTime + (1800 - TimeRest) ))
[[ $TimeRest -gt $((VAL_DUR - 1800)) ]] && CurrNodeUpdTime=$(( CurrNodeUpdTime - (1800 - (VAL_DUR - TimeRest) ) ))

echo "ElectionsSkip: $ElectionsSkip"
echo "TimeRest: $TimeRest"
echo "this node time shift: $UpdTimeShift"

echo "Current Elections Start  $Curr_Elect_Time / $(TD_unix2human $Curr_Elect_Time)"
echo "Nearest Elections Start  $NearElectionsID / $(TD_unix2human $NearElectionsID)"
echo "Next Elections Start     $NextElectionsID / $(TD_unix2human $NextElectionsID)"
echo "This node update time is $CurrNodeUpdTime / $(TD_unix2human $CurrNodeUpdTime)"

#=================================================
# String for cron
CronUpdTime="$(GET_M_H_D $CurrNodeUpdTime)"
echo
echo "Cron string:"
# shellcheck disable=SC2031
echo "$CronUpdTime * *    cd \"${SCRIPT_DIR}\" && ./Update_ALL.sh &>> \"${EVER_LOG_DIR}/validator.log\""

echo

exit 0
