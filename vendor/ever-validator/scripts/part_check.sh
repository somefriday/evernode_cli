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
echo "#################################### Check participations script ########################################"
SelfScriptName=$(basename "$0")
echo "INFO: $SelfScriptName BEGIN $(date +%s) / $(date  +'%F %T %Z')"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/functions.shinc"

#=================================================
echo
echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo

#=================================================
# Check compute_returned_stake in elector contract for both proxies
function proxy_recover_amount() {
    if [[ "$STAKE_MODE" == "depool" ]];then
        echo -e "\n---INFO: Check compute_returned_stake in elector contract for both proxies..."
        elector_addr="-1:3333333333333333333333333333333333333333333333333333333333333333"
        Depool_addr=$(cat "${KEYS_DIR}/depool.addr")
        Current_Depool_Info="$(Get_DP_Info "${Depool_addr}")"
        dp_proxy0=$(echo "$Current_Depool_Info" | jq -r ".proxies[0]")
        dp_proxy1=$(echo "$Current_Depool_Info" | jq -r ".proxies[1]")
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
    fi
}
# =====================================================
[[ "$STAKE_MODE" == "depool" ]] && Depool_addr="$(cat "${KEYS_DIR}/depool.addr")"
Validator_addr="$(cat "${KEYS_DIR}/${VALIDATOR_NAME}.addr")"

# =====================================================
# Get current elections ID
if ! elections_id="$(Get_Current_Elections_ID)";then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't get current elections ID!"
    echo "$elections_id"
    exit 1
fi
echo "INFO: Elections ID:      ${elections_id}"
[[ "$STAKE_MODE" == "depool" ]] && echo "INFO: DePool Address:    $Depool_addr"
echo "INFO: Validator Address: $Validator_addr"

if ! Engine_ADNL_Info="$(Get_Engine_ADNL)";then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't get Engine ADNL info! Check node configuration."
    echo "$Engine_ADNL_Info"
    exit 1
fi

if [[ "$Engine_ADNL_Info" == "null" ]];then
    echo "+++-WARNING(${SelfScriptName} line $LINENO): You have not participated in any elections yet!"
    echo
    exit 0
fi

if [[ $elections_id -gt 0 ]];then
    CURR_ELECTIONS_DIR="${ELECTIONS_WORK_DIR}/${elections_id}"
    INPL_CURR_ELECTIONS_DIR="${INPL_ELECTIONS_WORK_DIR}/${elections_id}"
else
    CURR_ELECTIONS_DIR="${ELECTIONS_WORK_DIR}/0"
    INPL_CURR_ELECTIONS_DIR="${INPL_ELECTIONS_WORK_DIR}/0"
fi

# =====================================================
# if ADNL key not provided, we will use ADNL key from the node configuration file
ADNL_KEY="$1"
Curr_ADNL_Key="$(echo "$Engine_ADNL_Info"|awk '{print $3}')"
# if it is first time participation in elections Next_ADNL_Key will be first in the list
[[ -z $Curr_ADNL_Key ]] && Curr_ADNL_Key="$(echo "$Engine_ADNL_Info"|awk '{print $1}')"
ADNL_KEY=${ADNL_KEY:=$Curr_ADNL_Key}
echo "INFO: Validator ADNL:    $ADNL_KEY"

# =====================================================
# if elections is closed, we will search in next validators list (p36) and then in current validators list (p34)
if [ "$elections_id" == "0" ]; then
    VALS_DEF="NEXT"
    echo
    date +"INFO: %F %T No current elections"
    # If last elections is closed but validator set not changed yet, we search in next validators list (p36
    Part_VAL="$(P36_ADNL_search "$ADNL_KEY")"
    if [[ "${Part_VAL}" == "null" ]];then
        # If validator set is changed, we search in current validators list (p34)
        Part_VAL="$(P34_ADNL_search "$ADNL_KEY")"
        VALS_DEF="CURRENT"
    fi
    
    # if ADNL key not found in current or next validators list
    FOUND_PUB_KEY="$(echo "$Part_VAL" |awk '{print $1}')"
    if [[ "$FOUND_PUB_KEY" == "absent" ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Your ADNL Key NOT FOUND in current or next validators list!!!"
        Send_msg_toTelBot "$VALIDATOR_NAME Server:" "$Tg_SOS_sign ###-ERROR: Your ADNL Key NOT FOUND in current or next validators list!!!" > /dev/null 2>&1
        echo "-----------------------------------------------------------------------------------------------------"
        echo
        exit 1
    fi

    # if ADNL key found in current or next validators list
    VAL_WEIGHT="$(echo "$Part_VAL" | awk '{print $2}')"
    echo
    echo "INFO: Found you in $VALS_DEF validators with weight $(echo "scale=3; ${VAL_WEIGHT} / 10000000000000000" | $CALL_BC)%"
    echo "INFO: Your public key: $FOUND_PUB_KEY"
    echo "INFO: Your   ADNL key: $(echo "$ADNL_KEY" | tr "[:upper:]" "[:lower:]")"
    echo "-----------------------------------------------------------------------------------------------------"
    proxy_recover_amount
    exit 0
fi

# =====================================================
# If an election is currently taking place, we will search for ADNL in the elector
echo
echo "Now is $(date +'%F %T %Z')"
new_val_round_date="$(echo "$elections_id" | gawk '{print strftime("%Y-%m-%d %H:%M:%S", $1)}')"

proxy_recover_amount

ADNL_FOUND="$(Elector_ADNL_Search "$ADNL_KEY")"
if [[ "$ADNL_FOUND" == "absent" ]];then
    echo -e "${Tg_SOS_sign}###-ERROR(${SelfScriptName} line $LINENO): Can't find you in participant list in Elector. account: ${Depool_addr}"
    Send_msg_toTelBot "$VALIDATOR_NAME Server:" \
        "$Tg_SOS_sign ###-ALARM: Can't find you in participant list in Elector for elections $elections_id ($new_val_round_date). account: ${Depool_addr}" > /dev/null 2>&1
    exit 1
fi

Your_Stake="$(echo "${ADNL_FOUND}" | awk '{print $1 / 1000000000}')"
You_PubKey="$(echo "${ADNL_FOUND}" | awk '{print $4}')"

echo "INFO: Elections ID:      ${elections_id}" >> "${CURR_ELECTIONS_DIR}/${elections_id}_elector-confirmed-bid.txt"
[[ "$STAKE_MODE" == "depool" ]] && echo "INFO: DePool Address:    $Depool_addr" >> "${CURR_ELECTIONS_DIR}/${elections_id}_elector-confirmed-bid.txt"
echo "INFO: Validator Address: $Validator_addr" >> "${CURR_ELECTIONS_DIR}/${elections_id}_elector-confirmed-bid.txt"

echo "---INFO: Your stake: $Your_Stake with ADNL: $(echo "$ADNL_KEY" | tr "[:upper:]" "[:lower:]")" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}_elector-confirmed-bid.txt"
echo "You public key in Elector: $You_PubKey" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}_elector-confirmed-bid.txt"
echo "You will start validate from $(TD_unix2human "${elections_id}")" | tee -a "${CURR_ELECTIONS_DIR}/${elections_id}_elector-confirmed-bid.txt"

# Send message to Telegram
TON_LIVE_URL=""
# "https://ton.live/validators?section=details&public_key=${You_PubKey}&key_block_num=undefined"
Send_msg_toTelBot "$VALIDATOR_NAME Server:" \
    "$Tg_CheckMark We are successfully participate in elections $elections_id ($new_val_round_date) \
    with stake $Your_Stake and ADNL:  $(echo "$ADNL_KEY" | tr "[:upper:]" "[:lower:]") ${TON_LIVE_URL}" > /dev/null 2>&1
echo "-----------------------------------------------------------------------------------------------------"
echo "$elections_id" > "${ELECTIONS_WORK_DIR}/curent_elections_id.txt"
# ==========================================
# Delete files older 7 days in elections log dirs
find "$ELECTIONS_WORK_DIR" -maxdepth 1 -type f -mtime +7 -name '*' -ls -exec rm -f {} \;  &>/dev/null
find "$ELECTIONS_HISTORY_DIR" -maxdepth 1 -type f -mtime +7 -name '*' -ls -exec rm -f {} \; &>/dev/null

# ==========================================

echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "================================================================================================"

exit 0
