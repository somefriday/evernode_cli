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
echo "###################################### Deploy LNIC #############################################"
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

# LNI_giver="0:c82cdbe63dbe05841af073d2d5f1299ee2f89430acfaa4048748b24841df167f"
# LNI_seed="estate indicate weekend embark witness grief loyal voice key achieve wreck quiz"
NANO_AMOUNT=$((5 * 1000000000))
BOUNCE="false"
Contract_Name="LastNodeInfo"

INIT_FILE="init_data.json"
DATA_FILE="update_data.json"

# everdev sol compile ${Contract_Name}.sol

Code="${Contract_Name}.tvc"
ABI="${Contract_Name}.abi.json"

KEYS_FILE="${Contract_Name}.keys.json"
ADDR_FILE="${Contract_Name}.addr"

if [[ ! -s $Code ]] || [[ ! -s $ABI ]] || [[ ! -s $KEYS_FILE ]];then
  echo "###-ERROR(line $LINENO): Check tvc, abi and keys files exist!"
  exit 1
fi

#  "name": "constructor",
#     "inputs": [
#         {
#             "components": [
#                 {"name": "NodeVersion", "type": "string"},
#                 {"name": "PrevNodeVersion", "type": "string"},
#                 {"name": "LastCommit", "type": "string"},
#                 {"name": "PrevCommit", "type": "string"},
#                 {"name": "SupportedBlock", "type": "uint8"},
#                 {"name": "PrevSupportedBlock", "type": "uint8"},
#                 {"name": "DockerImageVersion", "type": "uint16"},
#                 {"name": "DockerImageName", "type": "string"},
#                 {"name": "UpdateByCron", "type": "bool"},
#                 {"name": "UpdateStartTime", "type": "uint32"},
#                 {"name": "UpdateDuration", "type": "uint32"},
#                 {"name": "MinCLIversion", "type": "string"},
#                 {"name": "DisableOldNodeValidate", "type": "bool"}
#             ],
#             "name": "initial_node_info",
#             "type": "tuple"
#         },
#         {"name": "code_deploy_time", "type": "uint32"},
#         {"name": "info_deploy_time", "type": "uint32"},
#         {"name": "initial_ABI", "type": "string"}
#     ],
#     "outputs": []

# Prepare init data
cat <<_ENDCNT_ > $INIT_FILE
{
  "initial_node_info": {
    "NodeVersion": "000058017",
    "PrevNodeVersion": "000058013",
    "LastCommit": "b22220b55f8bb27af760f64b58971936191038d4",
    "PrevCommit": "a03cbcb87eef4392f09b8fd7474589341a4acf2c",
    "SupportedBlock": 54,
    "PrevSupportedBlock": 53,
    "DockerImageVersion": 457,
    "DockerImageName": "everx/ever-node:custom-ever-node",
    "UpdateByCron": false,
    "UpdateStartTime": 0,
    "UpdateDuration": 0,
    "MinCLIversion": "000040000",
    "DisableOldNodeValidate": false
},
  "code_deploy_time": 0,
  "info_deploy_time": 0,
  "initial_ABI": "xxx"
}

_ENDCNT_


jq "del(.initial_ABI, .code_deploy_time) | .[\"new_node_info\"] = .initial_node_info | .[\"new_info_time\"] = .info_deploy_time | del (.initial_node_info, .info_deploy_time)" "${INIT_FILE}" > "${DATA_FILE}"

code_deploy_time="$(date +%s)"
info_deploy_time="$(date +%s)"
xz -z -k -9 -c ${Contract_Name}.abi.json > ${Contract_Name}.abi.xz && xxd -ps ${Contract_Name}.abi.xz | tr -d '\n' > ${Contract_Name}.abi.hex

initial_ABI="$(cat ${Contract_Name}.abi.hex)"

yq e -i -oj ".code_deploy_time = ${code_deploy_time} | .info_deploy_time = ${info_deploy_time} | .initial_ABI = \"${initial_ABI}\""  ${INIT_FILE}

echo "Info for deploy:"
jq . $INIT_FILE

set -x
Contract_ADDR="$($CALL_CLI -j genaddr $Code --abi $ABI --setkey $KEYS_FILE --wc 0 --save | jq -r '.raw_address' | tee "${ADDR_FILE}")"
set +x
# --data ${INIT_FILE}
# Contract_ADDR=$(cat "${ADDR_FILE}")
echo
echo "Contract addr: $Contract_ADDR"
echo "      Network: $($CALL_CLI -j config --list |jq -r '.url')"
echo

ACCOUNT_INFO="$(Get_Account_Info "$Contract_ADDR")"
ACC_STATUS="$(echo "$ACCOUNT_INFO" |awk '{print $1}')"
if [[ "$ACC_STATUS" == "None" ]];then
    echo -e "${BoldText}${RedBack}Account does not exist! (no tokens, no code, nothing)${NormText}"
    echo "=================================================================================================="
    exit 1
fi
[[ "$ACC_STATUS" == "Uninit" ]] && ACC_STATUS="${BoldText}${YellowBack}Uninit${NormText}" || ACC_STATUS="${BoldText}${GreenBack}Deployed and Active${NormText}"

AMOUNT="$(echo "$ACCOUNT_INFO" |awk '{print $2}')"
ACC_LAST_OP_TIME=$(echo "$ACCOUNT_INFO" | gawk '{ print strftime("%Y-%m-%d %H:%M:%S", $3)}')
echo -e "Status: $ACC_STATUS"
echo "Has balance : $(echo "scale=3; $((AMOUNT)) / 1000000000" | $CALL_BC) tokens"
echo "Last operation time: $ACC_LAST_OP_TIME"


#### Address ready to deploy
read -rp "### CHECK INFO TWICE!!! Is this a right info?  (y/n)? " </dev/tty answer
case ${answer:0:1} in
    y|Y )
        echo "Deploing..."
    ;;
    * )
        echo "Cancelled."
        exit 1
    ;;
esac

#Deploy contract
set -x
$CALL_CLI deploy --wc 0 --abi ${ABI} --sign ${KEYS_FILE} ${Code} $(jq -c . ${INIT_FILE})
set +x

exit 0

####################s
## Examples:
# $CALL_CLI run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) getALLinfo {}
# $CALL_CLI run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) getLastNodeInfo {}
# $CALL_CLI run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) node_info {}
# $CALL_CLI run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) code_ver {}
# $CALL_CLI run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) code_updated_time {}
# $CALL_CLI run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) info_updated_time {}
# $CALL_CLI run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) ABI {}
# $CALL_CLI run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) ABI {}|jq -r '.ABI'|xxd -r -p > lnm.7z
# $CALL_CLI run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) getABI {}
# $CALL_CLI -j run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) getABI {}|jq -r '.ABI'|xxd -r -p > lnm.xz
# xz -d -c lnm.xz > lnm.abi.json
