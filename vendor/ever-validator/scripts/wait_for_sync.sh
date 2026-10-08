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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/functions.shinc"

echo
echo -e "$(DispEnvInfo)"
echo

SLEEP_TIMEOUT=$1
SLEEP_TIMEOUT=${SLEEP_TIMEOUT:="10"}
MAX_TIME_DIFF=10
Current_Net="${NETWORK_TYPE%%.*}"

second_sync=false

while(true); do
     TIME_DIFF=$(Get_TimeDiff)

    if [[ "$TIME_DIFF" == "Node Down" ]];then
        echo "${Current_Net} Time: $(date +'%F %T %Z') ###-ALARM! NODE IS DOWN." | tee -a "${NODE_LOGS_ARCH}/time-diff.log"
        # Send_msg_toTelBot "$VALIDATOR_NAME Server" "ALARM! NODE IS DOWN." &> /dev/null
        sleep $SLEEP_TIMEOUT
        continue
    fi
    if [[ "$TIME_DIFF" == "Error" ]];then
        echo "${Current_Net}:${NODE_WC} Time: $(date +'%F %T %Z') ###-ALARM! NODE return ERROR." | tee -a "${NODE_LOGS_ARCH}/time-diff.log"
        # Send_msg_toTelBot "$VALIDATOR_NAME Server" "ALARM! NODE return ERROR." &> /dev/null
        sleep $SLEEP_TIMEOUT
        continue
    fi

    if [[ "$TIME_DIFF" == "db_broken" ]];then
        echo "${Current_Net} Time: $(date +'%F %T %Z') ###-ALARM! node DB is BROKEN!" | tee -a "${NODE_LOGS_ARCH}/time-diff.log"
        Send_msg_toTelBot "$VALIDATOR_NAME Server" "ALARM! node DB is BROKEN!" &> /dev/null
        sleep $SLEEP_TIMEOUT
        continue
    fi

    STATUS=$(echo "$TIME_DIFF"|awk '{print $3}')
    if [[ "$STATUS" != "synchronization_by_blocks" ]] && [[ "$STATUS" != "synchronization_finished" ]];then
        echo "${Current_Net}:${NODE_WC} Time: $(date +'%F %T %Z') --- Current node status: $TIME_DIFF" | tee -a "${NODE_LOGS_ARCH}/time-diff.log"
    else
        MC_TIME_DIFF=$(echo "$TIME_DIFF"|awk '{print $1}')
        SH_TIME_DIFF=$(echo "$TIME_DIFF"|awk '{print $2}')
        VALIDATION=$(echo "$TIME_DIFF"|awk '{print $4}')
        echo "${Current_Net} Time: $(date +'%F %T %Z') TimeDiffs: MC - $MC_TIME_DIFF ; WC - $SH_TIME_DIFF ; VAL - $VALIDATION" | tee -a "${NODE_LOGS_ARCH}/time-diff.log"
        if [[ $MC_TIME_DIFF -le $MAX_TIME_DIFF ]] && [[ $SH_TIME_DIFF -le $MAX_TIME_DIFF ]];then
            if $second_sync;then
                exit 0
            fi
            second_sync=true
        fi
    fi
    sleep $SLEEP_TIMEOUT
done

exit 0
