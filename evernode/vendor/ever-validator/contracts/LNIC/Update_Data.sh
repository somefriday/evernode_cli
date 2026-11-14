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

set -eE
echo
echo "#################################### Update data in LNIC #######################################"
SelfScriptName=$(basename "$0")
echo "INFO: $SelfScriptName BEGIN $(date +%s) / $(date  +'%F %T %Z')"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if ! source "${SCRIPT_DIR}/../../scripts/env.sh"; then
    echo "###-ERROR: Can't find env.sh"
    exit 1
fi
# SCRIPT_DIR was modified by the previous line, so we have to load the functions from the actual SCRIPT_DIR - 
if ! source "${SCRIPT_DIR}/../../functions.shinc"; then
    echo "###-ERROR: Can't find functions.shinc"
    exit 1
fi

Contract_Name="LastNodeInfo"
DATA_FILE="update_data.json"
Contract_ABI="${Contract_Name}.abi.json"

# everdev sol compile ${Contract_Name}.sol

KEYS_FILE="${Contract_Name}.keys.json"
ADDR_FILE="${Contract_Name}.addr"

cat $DATA_FILE | jq ''

##################################################################################
read -rp "### CHECK INFO TWICE!!! Is this a right info?  (y/n)? " </dev/tty answer
case ${answer:0:1} in
    y|Y )
        echo "Updating..."
    ;;
    * )
        echo "Cancelled."
        exit 1
    ;;
esac
##################################################################################

[[ -f ${Contract_ABI} ]] || { echo "ABI file not found"; exit 1; }
new_info_time="$(date +%s)"
cat "${DATA_FILE}" | jq ".new_info_time = ${new_info_time}" > "${DATA_FILE}.tmp"
mv -f "${DATA_FILE}.tmp" "${DATA_FILE}"
$CALL_CLI call --abi ${Contract_ABI} --sign ${KEYS_FILE} "$(cat ${ADDR_FILE})" change_node_info ${DATA_FILE}

exit 0
