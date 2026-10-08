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
echo "################################### Update NODE Script #########################################"
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

#===========================================================
# Check github for new node release
Node_local_commit="$(git --git-dir="$NODE_SRC_DIR/.git" rev-parse HEAD 2>/dev/null)"
Node_remote_commit="$(git --git-dir="$NODE_SRC_DIR/.git" ls-remote 2>/dev/null | grep 'HEAD'|awk '{print $1}')"
Node_bin_commit="$("${NODE_BIN_DIR}/$NODE_BIN_NAME" -V | grep 'NODE git commit:' | awk '{print $5}')"
Node_bin_ver="$("${NODE_BIN_DIR}/$NODE_BIN_NAME" -V | grep 'Node, version' | awk '{print $4}')"
Node_SVC_ver="$("$CALL_CONS" -jc getstats 2>/dev/null|cat|jq -r '.node_version' 2>/dev/null|cat)"

# if settled certain commit (not master) in env.sh 
[[ "${NODE_GIT_COMMIT}" != "master" ]] && Node_remote_commit="${NODE_GIT_COMMIT}"

if [[ -z $Node_local_commit ]];then
    echo "###-ERROR(line $LINENO): Cannot get LOCAL node commit!"
    exit 1
fi
if [[ -z $Node_remote_commit ]];then
    echo "###-ERROR(line $LINENO): Cannot get REMOTE node commit!"
    exit 1
fi
if [[ "$Node_bin_commit" !=  "$Node_local_commit" ]];then
    echo "###-WARNING(line $LINENO): Commit from binary file is not equal git dir commit ($NODE_SRC_DIR)"
fi
if [[ "$Node_bin_ver" != "$Node_SVC_ver" ]];then
    echo "###-WARNING(line $LINENO): Running node version ($Node_SVC_ver) in service is not equal binary file version ($Node_bin_ver)!!"
fi
#===========================================================
# Update FreeBSD daemon script to avoide node service stuck
OS_SYSTEM=$(uname -s)
if [[ "$OS_SYSTEM" == "FreeBSD" ]];then
    "${SCRIPT_DIR}/setup_as_service.sh"
fi

#===========================================================
# check LNIC for new update and times
LNIC_present=false
Console_commit="$RCONS_GIT_COMMIT"
LNI_Info="$( get_LastNodeInfo )"
if [[ "$(echo "$LNI_Info"|tail -n 1)" ==  "none" ]];then
    echo "###-WARNING(line $LINENO): Last node info from contract is empty."
else
    LNIC_present=true
    Node_remote_commit=$(jq -r '.LastCommit' "${LNI_Info}")
    Console_commit=$(jq -r '.ConsoleCommit' "${LNI_Info}")
    echo "LNIC present. New node commit: $Node_remote_commit, Console commit: $Console_commit"
fi

#===========================================================
# Checking node need update
if [[ "$Node_remote_commit" == "$Node_local_commit" ]] && \
   [[ "$Node_remote_commit" == "$Node_bin_commit" ]] && \
   [[ "$Node_bin_ver" == "$Node_SVC_ver" ]];then
    echo "---INFO: The Node seems is up to date (ver $Node_bin_ver), but possible you have to update scripts..."
    echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date  +'%F %T %Z')"
    echo "================================================================================================"
    exit 0
fi

#===========================================================
# Checking if update is scheduled in LNIC
# if UpdateDuration == 0 just doing update now
# if no, check schedule
if $LNIC_present;then
    declare -i UpdateStartTime UpdateDuration CurrTime Validator_Upd_Ord CurrNodeUpdateTime
    UpdateStartTime=$(echo "$LNI_Info" | jq -r '.UpdateStartTime')
    CurrTime=$(date +%s)
    if [[ $UpdateStartTime -gt $CurrTime ]];then
        echo "###-ERROR(line $LINENO): Update time is not come yet. Net nodes updates will start from $(TD_unix2human $UpdateStartTime)"
        echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date  +'%F %T %Z')"
        echo "================================================================================================"
        exit 0
    fi
    UpdateDuration=$(echo "$LNI_Info" | jq -r '.UpdateDuration')
    Validator_addr=$(cat "${KEYS_DIR}/${VALIDATOR_NAME}.addr")
    Validator_Upd_Ord=$(( $(hex2dec "$(echo "$Validator_addr" | cut -c 33,34)") ))
    CurrNodeUpdateTime=$((UpdateDuration / 256 * Validator_Upd_Ord + UpdateStartTime))
    if [[ $CurrNodeUpdateTime -gt $CurrTime ]];then
        echo "###-ERROR(line $LINENO): Update time for your node is not come yet. Your node update time is $(TD_unix2human $CurrNodeUpdateTime)"
        echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date  +'%F %T %Z')"
        echo "================================================================================================"
        exit 0
    fi

    # set new commits in env.sh for Nodes_Build script
    sed -i.bak "s/export NODE_GIT_COMMIT=.*/export NODE_GIT_COMMIT=$Node_remote_commit/g" "${SCRIPT_DIR}/env.sh"
    # sed -i.bak "/ton-labs-node.git/,/\"NETWORK_TYPE\" == \"rfld.ton.dev\"/ s/export NODE_GIT_COMMIT=.*/export NODE_GIT_COMMIT=\"$Node_remote_commit\"/" "${SCRIPT_DIR}/env.sh"
    sed -i.bak "s/export RCONS_GIT_COMMIT=.*/export RCONS_GIT_COMMIT=$Console_commit/g" "${SCRIPT_DIR}/env.sh"
fi

echo "INFO: Node going to update from $Node_local_commit to new commit $Node_remote_commit"
Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_Warn_sign INFO: Node going to update from $Node_local_commit to new commit $Node_remote_commit" > /dev/null 2>&1

#===========================================================
# Get recommended Rust version from node repo
Node_Build_Rust_Version="$(curl curl https://raw.githubusercontent.com/tonlabs/ever-node/$Node_remote_commit/recomended_rust 2>/dev/null)"
V1=$(echo "$Node_Build_Rust_Version"|awk -F'.' '{print $1}')
V2=$(echo "$Node_Build_Rust_Version"|awk -F'.' '{print $2}')
V3=$(echo "$Node_Build_Rust_Version"|awk -F'.' '{print $3}')
if [[ $V1 =~ ^[[:digit:]]+$ ]] && [[ $V2 =~ ^[[:digit:]]+$ ]] && [[ $V3 =~ ^[[:digit:]]+$ ]];then
    declare -i Rust_Version_NUM
    Rust_Version_NUM=$(echo "$Node_Build_Rust_Version" | awk -F'.' '{printf("%d%03d%03d\n", $1,$2,$3)}')
    if [[ $Rust_Version_NUM -ne 0 ]];then
        sed -i.bak "s/export RUST_VERSION=.*/export RUST_VERSION=$Node_Build_Rust_Version/" "${SCRIPT_DIR}/env.sh"
        source "${SCRIPT_DIR}/env.sh"
    fi
fi

#===========================================================
# Update Node, node console, ever-cli and contracts

#################################
"${SCRIPT_DIR}/Nodes_Build.sh rust"
#################################

if [[ $? -gt 0 ]];then
    echo "###-ERROR(line $LINENO): Build update filed!"
    Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_SOS_sign ###-ERROR(line $LINENO): Node update filed!! Check ${NODE_LOGS_ARCH}/NodeUpdate.log for details." > /dev/null 2>&1
    exit 1
fi

Node_local_repo_commit="$(git --git-dir="$NODE_SRC_DIR/.git" rev-parse HEAD 2>/dev/null)"
Node_commit_from_bin="$("${NODE_BIN_DIR}/$NODE_BIN_NAME" -V | grep 'TON NODE git commit' | awk '{print $5}')"
EverNode_Version="$("${NODE_BIN_DIR}/$NODE_BIN_NAME" -V | grep -i 'TON Node, version' | awk '{print $4}')"
NodeSupBlkVer="$(${NODE_BIN_DIR}/$NODE_BIN_NAME -V | grep 'BLOCK_VERSION:' | awk '{print $2}')"

if [[ "${Node_local_repo_commit}" != "${Node_commit_from_bin}" ]];then
    echo "###-ERROR(line $LINENO): Build update filed! Repo commit (${Node_local_repo_commit}) not equal commit from binary (${Node_commit_from_bin})."
    Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_SOS_sign ###-ERROR(line $LINENO): Build update filed! Repo commit (${Node_local_repo_commit}) not equal commit from binary ${Node_commit_from_bin}." > /dev/null 2>&1
    exit 1
fi
Console_Version="$("${NODE_BIN_DIR}/console" -V | awk '{print $2}')"
CLI_Version="$(${NODE_BIN_DIR}/ever-cli -V | grep -i 'ever_cli' | awk '{print $2}')"

echo "INFO: All builded. Current versions: node ver: ${EverNode_Version} SupBlock: ${NodeSupBlkVer} node commit: ${Node_commit_from_bin}, console - ${Console_Version}, ever-cli - ${CLI_Version}"
Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_CheckMark INFO: All builded. Current versions: node ver: ${EverNode_Version} node commit: ${Node_commit_from_bin}, console - ${Console_Version}, ever-cli - ${CLI_Version}" > /dev/null 2>&1


echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "================================================================================================"

exit 0
