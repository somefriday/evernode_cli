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

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
if ! source "${SCRIPT_DIR}/../env.sh"; then
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

if [[ $NODE_IP_ADDR == "" ]];then
    echo -e "\n###-ERROR(line $LINENO): NODE_IP_ADDR variable is empty. Please run Initial_Setup.sh or set it manually in env.sh\n"
    exit 1
fi

echo
echo "################################# Rust node confugure script ###################################"
SelfScriptName=$(basename "$0")
echo "+++INFO: ${SelfScriptName} BEGIN $(date +%s) / $(date)"

#===========================================
# Configs source files
DFLT_CFG_FILE="${CONFIGS_DIR}/rnode/default_config.json"
LOG_CFG_FILE="${CONFIGS_DIR}/rnode/log_cfg.yml"
CONS_TMPLT_FILE="${CONFIGS_DIR}/rnode/console_template.json"

#===========================================
# Set network global config file
case $NETWORK_TYPE in
    main|mainnet)
        cp -f "${CONFIGS_DIR}/mainnet/$NET_GLOBAL_CFG_FILE_NAME" "${NODE_CFG_DIR}/"
        ;;
    net|devnet|testnet)
        cp -f "${CONFIGS_DIR}/devnet/$NET_GLOBAL_CFG_FILE_NAME" "${NODE_CFG_DIR}/"
        ;;
    *)
        echo "###-ERROR(${SelfScriptName}: line $LINENO): Unknown NETWORK_TYPE: $NETWORK_TYPE"
        exit 1
        ;;
esac

#===========================================
# Setup default_config
echo -n "---INFO: Prepare default_config from ${DFLT_CFG_FILE}..."
jq \
    ".log_config_name = \"$INPL_NODE_CFG_DIR/log_cfg.yml\" | \
    .ton_global_config_name = \"$INPL_NODE_CFG_DIR/$NET_GLOBAL_CFG_FILE_NAME\" | \
    .internal_db_path = \"$INPL_NODE_DB_DIR\" | \
    .ip_address = \"${NODE_IP_ADDR}:${ADNL_PORT}\" | \
    .control_server_port = $RCONSOLE_PORT" \
    "${DFLT_CFG_FILE}" > "${NODE_CFG_DIR}/default_config.json"
echo " ..DONE"

#===========================================
# Setup log_cfg.yml
echo -n "---INFO: Prepare log_cfg from ${LOG_CFG_FILE}..."
yq eval ".appenders.logfile.path = \"${INPL_NODE_LOG_DIR}/${NODE_LOG_FILE}\" | \
.appenders.rolling_logfile.path = \"${INPL_NODE_LOG_DIR}/${NODE_LOG_FILE}\" | \
.appenders.rolling_logfile.policy.roller.pattern = \"${INPL_NODE_LOG_DIR}/${NODE_LOG_FILE%.*}_{}.${NODE_LOG_FILE##*.}\"" \
"${LOG_CFG_FILE}" > "${NODE_CFG_DIR}/log_cfg.yml"
echo " ..DONE"

#===========================================
# Set node console keys
echo -n "---INFO: Prepare console_client_keys..."
# shellcheck disable=SC2086
${EXECUTE_BINARIES_PATH}/keygen > "${NODE_CFG_DIR}/${VALIDATOR_NAME}_console_client_keys.json"
jq -c '.public' "${NODE_CFG_DIR}/${VALIDATOR_NAME}_console_client_keys.json" > "${NODE_CFG_DIR}/console_client_public.json"
echo " ..DONE"

#===========================================
# Generate node and console configs
echo -n "---INFO: Genegate node config.json..."
RN_OUT="$($CALL_NODE --ckey "$(cat "${NODE_CFG_DIR}/console_client_public.json")" --process-conf-and-exit 2>&1)"
# TODO: Change after fix exit code in node
if echo "$RN_OUT"|grep -q "Can't generate";then ExitCode=1;else ExitCode=0;fi
#ExitCode=$?
if [ $ExitCode -ne 0 ] || [[ ! -f "${NODE_CFG_DIR}/config.json" ]]; then
    echo "###-ERROR(${SelfScriptName}: line $LINENO): ${NODE_CFG_DIR}/config.json does not created!"
    echo "ERROR: $RN_OUT"
    exit 1
fi
if [ $ExitCode -ne 0 ] ||  [[ ! -f "${NODE_CFG_DIR}/console_config.json" ]]; then
    echo "###-ERROR(${SelfScriptName}: line $LINENO): ${NODE_CFG_DIR}/console_config.json does not created!"
    echo "ERROR: $RN_OUT"
    exit 1
fi

#===========================================
# Set keys in console.json
CONS_CFG_TMP="$(jq ".client_key = $(jq .private "${NODE_CFG_DIR}/${VALIDATOR_NAME}_console_client_keys.json")" "${NODE_CFG_DIR}/console_config.json")"
jq ".config = ${CONS_CFG_TMP}" "${CONS_TMPLT_FILE}" > "${NODE_CFG_DIR}/console.json"

echo " ..DONE"
echo
echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date)"
echo "================================================================================================"

exit 0
