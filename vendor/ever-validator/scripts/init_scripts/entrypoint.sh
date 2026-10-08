#!/usr/bin/env bash

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

if [[ ! -f /.dockerenv ]]; then
    echo "###-ERROR(${BASH_SOURCE[0]}: line $LINENO): This script is intended to run in a container only!"
    exit 1
fi

umask 000
pgrep -x "cron" > /dev/null || cron
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
SelfScriptName=$(basename "$0")
if ! source "${SCRIPT_DIR}/../env.sh"; then
    echo "###-ERROR(${SelfScriptName}: line $LINENO): Can't load env.sh"
    exit 1
fi

# Chech yq and install if not present
if ! yq -V 2>/dev/null | cat | grep -q version; then
    echo "+++WARNING: yq not found. Installing..."
    if ! curl https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_amd64 -o /usr/local/bin/yq &>/dev/null; then
        echo "###-ERROR(${SelfScriptName}: line $LINENO): Failed to download yq"
        exit 1
    fi
    chmod +x /usr/local/bin/yq
fi

Curr_Timestamp="$(date +%Y-%m-%d_%H-%M-%S)"
CONTAINER_RUN_MODE=$1
case $CONTAINER_RUN_MODE in
    "sync")
        echo "---INFO: Running in sync mode"
        echo "+++Node log directory NODE_LOG_DIR: ${NODE_LOG_DIR}"
        echo "+++Node config directory NODE_CFG_DIR: ${NODE_CFG_DIR}"
        echo "+++Node CALL_NODE: ${CALL_NODE}"
        crontab -r
        # Write timestamp to logs
        echo -e "\n$Curr_Timestamp" | tee -a "${NODE_LOG_DIR}/stdout.log" >> "${NODE_LOG_DIR}/stderr.log"
        # Run node
        export RUST_BACKTRACE=full
        exec ${CALL_NODE} >> "${NODE_LOG_DIR}/stdout.log" 2>> "${NODE_LOG_DIR}/stderr.log"
        if [[ $? -ne 0 ]]; then
            echo "###-ERROR(${SelfScriptName}: line $LINENO): It should not reach here! Node exited unexpectedly!"
            echo "  Check node config, global config and statsd"
            echo "  And look into ${NODE_LOG_DIR}/stdout.log and ${NODE_LOG_DIR}/stderr.log"
        fi
        ;;
    "validator")
        echo "---INFO: Running in validator mode"
        # Setup crontab for validation and auto-update if enabled
        echo \
        "*/${CRONTAB_INTERVAL} * * * * 	${SCRIPT_DIR}/prepare_elections.sh >> ${VALIDATOR_LOG_DIR}/${VALIDATOR_LOG_FILE_NAME} 2>&1 && sleep 120 && ${SCRIPT_DIR}/take_part_in_elections.sh >> ${VALIDATOR_LOG_DIR}/${VALIDATOR_LOG_FILE_NAME} 2>&1" \
        | crontab -u root -
        # "${SCRIPT_DIR}/crontab_setup.sh" | tee -a "${NODE_LOG_DIR}/crontab.log"
        # Write timestamp to logs
        echo -e "\n$Curr_Timestamp" | tee -a "${NODE_LOG_DIR}/stdout.log" >> "${NODE_LOG_DIR}/stderr.log"
        # Run node
        export RUST_BACKTRACE=full
        exec ${CALL_NODE} >> "${NODE_LOG_DIR}/stdout.log" 2>> "${NODE_LOG_DIR}/stderr.log"    
        if [[ $? -ne 0 ]]; then
            echo "###-ERROR(${SelfScriptName}: line $LINENO): It should not reach here! Node exited unexpectedly!"
            echo "  Check node config, global config and statsd"
            echo "  And look into ${NODE_LOG_DIR}/stdout.log and ${NODE_LOG_DIR}/stderr.log"
        fi
        ;;
    "dapp")
        # bash in docker: 
        #   source env.sh
        #   docker exec -it $DOCKER_NODE_CONTAINER_NAME /bin/bash
        echo "---INFO: Running in DAPP mode"
        echo "+++Node log directory NODE_LOG_DIR: ${NODE_LOG_DIR}"
        echo "+++Node config directory NODE_CFG_DIR: ${NODE_CFG_DIR}"
        echo "+++Node CALL_NODE: ${CALL_NODE_KAFKA}"
        crontab -r
        # Write timestamp to logs
        echo -e "\n$Curr_Timestamp" | tee -a "${NODE_LOG_DIR}/stdout.log" >> "${NODE_LOG_DIR}/stderr.log"
        # Run node
        export RUST_BACKTRACE=full
        exec ${CALL_NODE_KAFKA} >> "${NODE_LOG_DIR}/stdout.log" 2>> "${NODE_LOG_DIR}/stderr.log"
        if [[ $? -ne 0 ]]; then
            echo "###-ERROR(${SelfScriptName}: line $LINENO): It should not reach here! Node exited unexpectedly!"
            echo "  Check node config, global config, statsd and kafka settings"
            echo "  Also check if kafka is running and reachable"
            echo "  And look into ${NODE_LOG_DIR}/stdout.log and ${NODE_LOG_DIR}/stderr.log"
        fi
        ;;
    "init")
        echo "---INFO: Running in init mode"
        crontab -r
        # Setting up node configs and exit
        "${SCRIPT_DIR}/init_scripts/R_gen_init_configs.sh" | tee -a "${NODE_LOG_DIR}/config_setup.log" 
        exit 0
        ;;
    "bash")
        echo "---INFO: Running in bash mode"
        tail -f /dev/null
        ;;
    *)
        echo "###-ERROR(ERROR(${SelfScriptName}: line $LINENO): Unknown run mode: $CONTAINER_RUN_MODE"
        exit 1
        ;;
esac
