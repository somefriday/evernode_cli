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
echo "################################## Update env.sh Script ########################################"
SelfScriptName=$(basename "$0") && export SelfScriptName
echo "INFO: $SelfScriptName BEGIN $(date +%s) / $(date +'%F %T %Z')"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
if ! source "${SCRIPT_DIR}/env.sh"; then
    log_error_stack "Can't load env.sh"
    exit 1
fi
#================================================================
# Set atomic lock
LOCK_FILE="${ELECTIONS_WORK_DIR}/${SelfScriptName}.lock"
# Get file descriptor for lock
exec 200>"$LOCK_FILE"
# Try to get lock
if ! flock -n 200; then
    log_error_stack "Script already running. Exiting..."
    exit 1
fi
# Release lock on exit
trap 'flock -u 200' EXIT
#================================================================
# shellcheck source=functions.shinc
source "${SCRIPT_DIR}/functions.shinc"

#################################################################
# Set new environment variables in env.sh
sed -i.bak "s|export RUST_VERSION=.*|export RUST_VERSION=\"1.81.0\"|; \
            s|export Main_DApp_URL=.*|export Main_DApp_URL=\"https://mainnet.evercloud.dev\"|; \
            s|export MainNet_DApp_List=.*|export MainNet_DApp_List=\"https://mainnet.evercloud.dev,https://gra01.main.everos.dev,https://lim01.main.everos.dev\"|; \
            s|export DevNet_DApp_URL=.*|export DevNet_DApp_URL=\"https://devnet.evercloud.dev\"|; \
            s|export DevNet_DApp_List=.*|export DevNet_DApp_List=\"https://devnet.evercloud.dev,https://eri01.net.everos.dev,https://gra01.net.everos.dev\"|; \
            s|export MIN_CLI_VERSION=.*|export MIN_CLI_VERSION=\"0.40.0\"|; \
            s|export NODE_GIT_REPO=.*|export NODE_GIT_REPO=\"https://github.com/tonlabs/ever-node.git\"|g; \
            s|export RCONS_GIT_REPO=.*|export RCONS_GIT_REPO=\"https://github.com/tonlabs/ever-node-tools.git\"|g; \
            s|export CLI_GIT_REPO=.*|export CLI_GIT_REPO=\"https://github.com/tonlabs/ever-cli.git\"|; \
            s|export Node_Blk_Min_Ver=.*|export Node_Blk_Min_Ver=48|" "${SCRIPT_DIR}/env.sh"

source "${SCRIPT_DIR}/env.sh"
#################################################################
# Get new environment variables for node update
if ! LNI_JSON="$(get_LastNodeInfo)"; then
    result=$?
    case $result in
        1) echo "###-WARNING(line $LINENO): LNIC_ADDRESS is empty!" ;;
        2) echo "###-WARNING(line $LINENO): LNIC account not found." ;;
        3) echo "###-WARNING(line $LINENO): Error getting LNIC state." ;;
        4) echo "###-WARNING(line $LINENO): Failed to decode LNIC ABI from HEX to binary" ;;
        5) echo "###-WARNING(line $LINENO): Failed to decompress LNIC ABI" ;;
        6) echo "###-WARNING(line $LINENO): Unknown compression format in LNIC ABI." ;;
        7) echo "###-WARNING(line $LINENO): Cannot get LNIC ABI from contract boc." ;;
        8) echo "###-WARNING(line $LINENO): Last node info from contract is empty." ;;
        *) echo "###-WARNING(line $LINENO): Unknown error." ;;
    esac
    log_error_stack "Can't get last node info"
    Send_msg_toTelBot "${HOSTNAME} Server" "${Tg_SOS_sign} ###-ERROR(${SelfScriptName} line $LINENO): Can't get last node info!$ ${Tg_SOS_sign}" 2>&1
    exit 1
fi

New_Node_Commit=$(jq -r '.LastCommit' <<< "${LNI_JSON}")
Node_repository=$(jq -r '.NodeRepository' <<< "${LNI_JSON}")
New_Node_SupportedBlock=$(jq -r '.SupportedBlock' <<< "${LNI_JSON}")
New_Node_DockerImageTAG=$(jq -r '.DockerImageTAG' <<< "${LNI_JSON}")
New_Node_DockerImageRepo=$(jq -r '.DockerImageRepo' <<< "${LNI_JSON}")
New_Node_DockerHUB_User="$(cut -d'/' -f1 <<< "${New_Node_DockerImageRepo}")"
New_Node_DockerHUB_Repo="$(cut -d'/' -f2 <<< "${New_Node_DockerImageRepo}")"
New_MinCLIversion=$(jq -r '.MinCLIversion' <<< "${LNI_JSON}")

# Set new environment variables in env.sh
CurrTimeStamp=$(date +%Y-%m-%d_%H-%M-%S)
# Backup env.sh
cp "${SCRIPT_DIR}/env.sh" "${VALIDATOR_LOG_DIR}/env.sh.${CurrTimeStamp}"
# Clean old env.sh backups
find "${VALIDATOR_LOG_DIR}" -name 'env.sh.*' -type f -printf '%T@ %p\n' | sort -nr | tail -n +11 | cut -d' ' -f2- | xargs rm --

# Set new environment variables in env.sh
sed -i.bak "
            s|export NODE_GIT_REPO=.*|export NODE_GIT_REPO=\"${Node_repository}\"|g; \
            s|export NODE_GIT_COMMIT=.*|export NODE_GIT_COMMIT=\"${New_Node_Commit}\"|; \
            s|export Node_Blk_Min_Ver=.*|export Node_Blk_Min_Ver=${New_Node_SupportedBlock}|; \
            s|export DOCKER_IMAGE_TAG=.*|export DOCKER_IMAGE_TAG=\"${New_Node_DockerImageTAG}\"|; \
            s|export DOCKERHUB_REPO=.*|export DOCKERHUB_REPO=\"${New_Node_DockerHUB_Repo}\"|; \
            s|export DOCKERHUB_USER=.*|export DOCKERHUB_USER=\"${New_Node_DockerHUB_User}\"|; \
            s|export MIN_CLI_VERSION=.*|export MIN_CLI_VERSION=\"${New_MinCLIversion}\"|; \
            " "${SCRIPT_DIR}/env.sh"

if [[ -z "$DAPP_Project_id" ]];then
    Send_msg_toTelBot "${VALIDATOR_NAME} Server" "${Tg_Exclaim_sign} $(cat "${SCRIPT_DIR}/Update_Info.txt") ${Tg_Exclaim_sign}" > /dev/null  2>&1
fi

echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "================================================================================================"

exit 0
