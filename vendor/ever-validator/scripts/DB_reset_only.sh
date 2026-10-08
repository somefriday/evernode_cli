#!/usr/bin/env bash
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

echo
echo "################################# Node Reset Database script ###################################"
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
# shellcheck source=functions.shinc
source "${SCRIPT_DIR}/functions.shinc"
echo
echo "Current Time: $(date +'%F %T %Z')"
echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo

#===========================================
# Stop node service or docker container depending on the RUN_MODE
if [[ "$RUN_MODE" == "service" ]]; then
    echo -n "---INFO: Stopping node service ..."
    sudo service evernode stop
    sleep 5
    echo " ..DONE"
elif [[ "$RUN_MODE" == "docker" ]]; then
    echo -n "---INFO: Stopping node docker container ..."
    pushd "${DOCKER_NODE_DIR}" || exit
    docker-compose down -t 600
    popd || exit
    sleep 5
    echo " ..DONE"
else
    echo "###-ERROR: Unknown RUN_MODE: $RUN_MODE"
    exit 1
fi

#===========================================
# Save all configs
mkdir -p "${NODE_TOP_DIR}/BackUps"
echo -n "---INFO: Save node configs to ${NODE_TOP_DIR}/BackUps/node_configs_${Curr_UnixTime} ..."
sudo cp -r "${NODE_CFG_DIR}" "${NODE_TOP_DIR}/BackUps/node_configs_${Curr_UnixTime}"
echo " ..DONE"

#===========================================
# Delete node DB folder
echo -n "---INFO: Delete current DB ..."
sudo rm -rf "${NODE_DB_DIR}"/*
echo " ..DONE"

#===========================================
# Start node service or docker container depending on the RUN_MODE
if [[ "$RUN_MODE" == "service" ]]; then
    echo -n "---INFO: Starting node service ..."
    sudo service evernode start
    sleep 5
    echo " ..DONE"
elif [[ "$RUN_MODE" == "docker" ]]; then
    echo -n "---INFO: Starting node docker container ..."
    pushd "${DOCKER_NODE_DIR}" || exit
    docker-compose up -d --remove-orphans
    popd || exit
    sleep 5
    echo " ..DONE"
else
    echo "###-ERROR: Unknown RUN_MODE: $RUN_MODE"
    exit 1
fi

echo
echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "================================================================================================"

exit 0
