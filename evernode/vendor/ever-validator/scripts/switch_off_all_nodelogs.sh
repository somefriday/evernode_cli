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

echo -n "---INFO: Prepare log_cfg for absolutely no logging..."

cp "${NODE_CFG_DIR}/log_cfg.yml" "${SCRIPT_DIR}/log_cfg.tmp"

if [[ "$(uname -s)" == "Linux" ]];then
    sed -i \
        -e 's/level: info/level: off/g'  \
        -e 's/level: trace/level: off/g' \
        -e 's/level: debug/level: off/g' \
        -e 's/level: error/level: off/g' \
    "${SCRIPT_DIR}/log_cfg.tmp"
else
    sed -i.bak \
        -e 's/level: info/level: off/g'  \
        -e 's/level: trace/level: off/g' \
        -e 's/level: debug/level: off/g' \
        -e 's/level: error/level: off/g' \
    "${SCRIPT_DIR}/log_cfg.tmp"
fi

mv -f "${SCRIPT_DIR}/log_cfg.tmp"  "${NODE_CFG_DIR}/log_cfg.yml"
echo " ..DONE"

exit 0
