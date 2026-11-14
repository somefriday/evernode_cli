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

echo "################################# get_LastNodeInfo script ###################################"
SelfScriptName=$(basename "$0")
echo "INFO: $SelfScriptName BEGIN $(date +%s) / $(date  +'%F %T %Z')"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if ! source "${SCRIPT_DIR}/env.sh"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't load env.sh"
    exit 1
fi
source "${SCRIPT_DIR}/functions.shinc"

#=================================================
echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo

TMP_DIR=$(mktemp -d -p "${EVER_TMP_DIR}" 2>/dev/null) || \
{ echoerr "###-ERROR(${FUNCNAME[1]}-${FUNCNAME[0]} line $LINENO): Cannot make temporary folder!"; return 1; }

#=================================================
# Get LNIC boc

if [[ "$(Get_Account_Info "$LNIC_ADDRESS"|awk '{print $1}')" != "Active" ]];then
    echo "###-ERROR(line $LINENO): LNIC account not found. Can't continue. Sorry."
    exit 1
else
    OUTPUT="$(Get_SC_current_state "$LNIC_ADDRESS")"
    if [[ $? -ne 0 ]] || [[ -z  "$(echo "$OUTPUT" | grep 'written StateInit of account')" ]]
    then
        echo "###-ERROR(line $LINENO): Cannot get LNIC account state. Can't continue. Sorry."
        exit 1
    fi
fi
#=================================================
# Get LNIC ABI from contract
# LastNodeInfo.abi.json
GetABI=$(jq -n '{
      "ABI version": 2,
      "version": "2.2",
      "header": ["time", "expire"],
      "functions": [
        {"name": "getABI", "inputs": [], "outputs": [{"name": "ABI_7z_hex", "type": "string"}]},
        {"name": "ABI", "inputs": [], "outputs": [{"name": "ABI_7z_hex", "type": "string"}]}
      ],
      "data": [],
      "events": [],
      "fields": [{"name": "ABI_7z_hex", "type": "string"}]
    }')
echo "$GetABI" > "${TMP_DIR}/Get_ABI.json"


$CALL_CLI -j run --boc "${TMP_DIR}/${LNIC_ADDRESS##*:}.boc" --abi Get_ABI.json ABI {} | jq -r '.ABI_7z_hex' > "${TMP_DIR}/LNIC_ABI_7z_hex.txt"
xxd -r -p "${TMP_DIR}/LNIC_ABI_7z_hex.txt" > "${TMP_DIR}/LNIC_ABI.7z"
$CALL_7Z x -y "${TMP_DIR}/LNIC_ABI.7z" > /dev/null 2>&1

ABI="${TMP_DIR}/LastNodeInfo.abi.json"
if [[ ! -e "${ABI}" ]];then
    echo "###-ERROR(line $LINENO): Cannot get LNIC ABI from state. Can't continue. Sorry."
    exit 1
fi

#=================================================
# Get Last node info and update schedule
LNI_JSON="$($CALL_CLI -j run --boc "${TMP_DIR}/${LNIC_ADDRESS##*:}.boc" --abi "${ABI}" node_info {})"

echo "${LNI_JSON}"

rm -f ${LNIC_ADDRESS##*:}.boc Get_ABI.json LNIC_ABI_7z_hex.txt LNIC_ABI.7z LastNodeInfo.abi.json

exit 0
