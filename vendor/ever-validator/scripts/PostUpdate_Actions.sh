#!/usr/bin/env bash
# shellcheck source=env.sh
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
echo "##################################### Postupdate Script ########################################"
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
source "${SCRIPT_DIR}/functions.shinc"

#===========================================================
#  Update network global config
"${SCRIPT_DIR}/nets_config_update.sh"

#===========================================================
# Check node version for DB reset
Node_bin_ver="$("${CALL_NODE}" -V | grep 'Node, version' | awk '{print $4}')"
Node_bin_ver_NUM=$(echo "$Node_bin_ver" | awk -F'.' '{printf("%d%03d%03d\n", $1,$2,$3)}')
Node_SVC_ver="$("$CALL_CONS" -jc getstats 2>/dev/null|cat|jq -r '.node_version' 2>/dev/null|cat)"
Node_SVC_ver_NUM=$(echo "$Node_SVC_ver" | awk -F'.' '{printf("%d%03d%03d\n", $1,$2,$3)}')

Node_bin_ver_NUM=$((10#${Node_bin_ver_NUM}))
Node_SVC_ver_NUM=$((10#${Node_SVC_ver_NUM}))
#########################
Chng_Config_ver=000055063
#########################
Chng_Config_ver=$((10#${Chng_Config_ver}))

#===========================================================
# For node ver >= 0.55.63 we have to change config.json
if [[ $Node_bin_ver_NUM -ge $Chng_Config_ver ]] && \
   [[ $Node_bin_ver_NUM -ne $Node_SVC_ver_NUM ]];then
    # Fix orphographic error in config.json
    sed -i.bak 's/prefill_cells_cunters/prefill_cells_counters/' "${NODE_CFG_DIR}/config.json"

    # Backup node config file
    # source ./env.sh
    Timestamp="$(date +%Y-%m-%d_%H-%M-%S)"
    cp "${NODE_CFG_DIR}/config.json" "${NODE_LOGS_ARCH}/config.json.${Timestamp}"
    # Set new parametrs in config.json
    Garbage_Collector='{
        "enable_for_archives": true,
        "archives_life_time_hours": 48,
        "enable_for_shard_state_persistent": true,
        "cells_gc_config": {
          "gc_interval_sec": 900,
          "cells_lifetime_sec": 1800
        }
    }'
    Remp_Config='{
        "client_enabled": true,
        "remp_client_pool": null,
        "service_enabled": true,
        "message_queue_max_len": 10000,
        "max_incoming_broadcast_delay_millis": 0
    }'
    Cells_DB_Config='{
        "states_db_queue_len": 1000,
        "max_pss_slowdown_mcs": 750,
        "prefill_cells_counters": false,
        "cache_cells_counters": true,
        "cache_size_bytes": 4294967296
    }'

    yq e -i -o json \
        ".gc = $Garbage_Collector | \
         .cells_db_config = $Cells_DB_Config | \
         .remp = $Remp_Config | \
         .states_cache_mode = \"Moderate\" | \
         .states_cache_cleanup_diff = 1000 | \
         .skip_saving_persistent_states =  false | \
         .restore_db = true | \
         .low_memory_mode = true" \
        "${NODE_CFG_DIR}/config.json"

    # Info messages
    echo "${Tg_Warn_sign} ATTENTION: The node will restart and may be out of sync temporarily! "
    Send_msg_toTelBot "$VALIDATOR_NAME Server" "${Tg_Warn_sign} ATTENTION: The node will restart and may be out of sync temporarily!" > /dev/null 2>&1

    # Clean catchain's garbage files
    Catchains_Dir="${NODE_DB_DIR}/catchains"
    find ${Catchains_Dir}/ -depth -type f \( -name "candidates*" -o -name "catchainreceiver*" \) -mtime +2 -exec rm -f {} \;
    find ${Catchains_Dir}/ -depth -type d \( -name "candidates*" -o -name "catchainreceiver*" \) -mtime +2 -exec rm -rf {} \;

    sudo service evernode restart
    sleep 2
    if [[ -z "$(pgrep $NODE_BIN_NAME)" ]];then
        echo "###-ERROR(line $LINENO): Node process not started!"
        Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_SOS_sign ###-ERROR(line $LINENO): Node process not started!" > /dev/null 2>&1
        exit 1
    fi
    "${SCRIPT_DIR}/wait_for_sync.sh"

    #===========================================================
    # Check and show the Node version
    NODE_BUILD_INFO=$("${CALL_NODE}" -V )
    Node_bin_commit="$(echo "$NODE_BUILD_INFO" | grep 'NODE git commit:' | awk '{print $5}')"
    EverNode_Version="$(echo "$NODE_BUILD_INFO" | grep -i 'TON Node, version' | awk '{print $4}')"
    NodeSupBlkVer="$(echo "$NODE_BUILD_INFO" | grep 'BLOCK_VERSION:' | awk '{print $2}')"
    Console_Version="$(echo "$NODE_BUILD_INFO" | awk '{print $2}')"
    CLI_Version="$("${CALL_CLI}" -V | grep -i 'ever_cli' | awk '{print $2}')"
    echo "INFO: Node updated. Service restarted. Current versions: node ver: ${EverNode_Version} SupBlock: ${NodeSupBlkVer} node commit: ${Node_bin_commit}, console - ${Console_Version}, ever-cli - ${CLI_Version}"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_CheckMark INFO: Node updated. Service restarted. Current versions: node ver: ${EverNode_Version} node commit: ${Node_bin_commit}, console - ${Console_Version}, ever-cli - ${CLI_Version}" > /dev/null 2>&1
    "${SCRIPT_DIR}/take_part_in_elections.sh"
    "${SCRIPT_DIR}/part_check.sh"
    "${SCRIPT_DIR}/next_elect_set_time.sh"
fi

#===========================================================
#
# ${SCRIPT_DIR}/DB_Repair_Actions.sh
#
#===========================================================

echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "================================================================================================"

exit 0
