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

set -o pipefail

echo
echo "#################################### Full update Script ########################################"
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
# Check if we in git repo
if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    echoerr "###-ERROR(${SelfScriptName} line $LINENO): Not a git repository!"
    exit 1
fi

#===========================================================
# Get scripts update info
if ! Scripts_local_commit="$(git --git-dir="${SCRIPT_DIR}/../.git" rev-parse HEAD 2>/dev/null)"; then
    echoerr "###-ERROR(${SelfScriptName} line $LINENO): Cannot get LOCAL Scripts commit!"
    exit 1
fi
if [[ -z $Scripts_local_commit ]];then
    echoerr "###-ERROR(${SelfScriptName} line $LINENO): Cannot get LOCAL Scripts commit!"
    exit 1
fi

if ! Scripts_remote_commit="$(git --git-dir="${SCRIPT_DIR}/../.git" ls-remote 2>/dev/null | grep 'HEAD'|awk '{print $1}')"; then
    echoerr "###-ERROR(${SelfScriptName} line $LINENO): Cannot get REMOTE Scripts commit!"
    exit 1
fi
if [[ -z $Scripts_remote_commit ]];then
    echoerr "###-ERROR(${SelfScriptName} line $LINENO): Cannot get REMOTE Scripts commit!"
    exit 1
fi

###############################################################
#===========================================================
# If local and remote commits differ and auto-update is disabled, warn the user
if [[ "$Scripts_local_commit" != "$Scripts_remote_commit" ]] && [[ "$Enable_Scripts_Autoupdate" != "true" ]]; then
    echo '---WARN: Set Enable_Node_Autoupdate to true in env.sh for automatically security updates!! If you fully trust me, you can enable autoupdate scripts in env.sh by set variable "Enable_Scripts_Autoupdate" to "true"'
    if ${newReleaseSndMsg};then
        Send_msg_toTelBot "$VALIDATOR_NAME Server" \
            "$Tg_Warn_sign"+'WARN: Security info! **NEW** release arrived! But Enable_Node_Autoupdate settled to false and you should upgrade node manually as fast as you can! If you fully trust me, you can enable autoupdate scripts in env.sh by set variable "Enable_Scripts_Autoupdate" to "true"' > /dev/null 2>&1
    fi
fi
###############################################################

#===========================================================
# Update scripts if auto-update is enabled, otherwise send a message about the update
if ${Enable_Scripts_Autoupdate};then
    if [[ "$Scripts_local_commit" == "$Scripts_remote_commit" ]]; then
        echo "---INFO: Scripts is up to date"
    else
        if $Enable_Scripts_Autoupdate ;then
            echo "---INFO: SCRIPTS going to update from $Scripts_local_commit to new commit $Scripts_remote_commit"
            Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_Warn_sign INFO: SCRIPTS going to update from $Scripts_local_commit to new commit $Scripts_remote_commit" > /dev/null 2>&1
            Remote_Repo_URL="$(git remote get-url origin)"
            echo "---INFO: Update scripts from repo $Remote_Repo_URL"

            #=======================================
            # Save critical configuration files before update
            CurrTimeStamp=$(date +%Y-%m-%d_%H-%M-%S)
            BackupDir="${HOME}/BackUp/${CurrTimeStamp}"
            mkdir -p "${BackupDir}"
            cp -f "${SCRIPT_DIR}/env.sh" "${BackupDir}/"
            # cp -f "${SCRIPT_DIR}/TlgChat.json" "${BackupDir}/" |cat
            cp -f "${SCRIPT_DIR}/RC_Addr_list.json" "${BackupDir}/"

            # Reset any local changes and perform a fast-forward pull to update scripts
            git reset --hard
            git pull --ff-only

            # Restore the saved configuration files after the update
            cp -f "${BackupDir}/env.sh" "${SCRIPT_DIR}/"
            # cp -f "${BackupDir}/TlgChat.json" "${SCRIPT_DIR}/" |cat
            cp -f "${BackupDir}/RC_Addr_list.json" "${SCRIPT_DIR}/"
            #=======================================

            #################################################################
            # # Update env.sh to accommodate new node version
            if ! "${SCRIPT_DIR}/Update_ENV.sh"; then
                echoerr "###-ERROR(${SelfScriptName} line $LINENO): Update_ENV.sh failed!"
                exit 1
            fi
            source "${SCRIPT_DIR}/env.sh"
            #################################################################

            cat "${SCRIPT_DIR}/Update_Info.txt"
            echo
            echo "---INFO: SCRIPTS updated. Files env.sh RC_Addr_list.json keeped."
            Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_CheckMark $(cat "${SCRIPT_DIR}/Update_Info.txt")" > /dev/null 2>&1
            Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_CheckMark INFO: SCRIPTS updated. Files env.sh RC_Addr_list.json keeped." > /dev/null 2>&1
        else
            echo '---WARN: Scripts repo was updated. Please check it and update by hand. If you fully trust me, you can enable autoupdate scripts in env.sh by set variable "Enable_Scripts_Autoupdate" to "true"'
            Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_Warn_sign"+'WARN: Scripts repo was updated. Please check it and update. If you fully trust me, you can enable autoupdate scripts in env.sh by set variable "Enable_Scripts_Autoupdate" to "true"' > /dev/null 2>&1
        fi
    fi
fi

#===========================================================
# Update NODE if auto-update is enabled
if ${Enable_Node_Autoupdate}; then
    case "$RUN_MODE" in
        service)
            if ! "${SCRIPT_DIR}/Update_Node_to_new_release.sh"; then
                log_error_stack "Update Node service to new release failed"
                exit 1
            fi
            ;;
        docker)
            if ! "${SCRIPT_DIR}/init_scripts/Docker_Setup.sh"; then
                log_error_stack "Update Node docker to new release failed"
                exit 1
            fi
            ;;
        offline)
            # In this case, update ever-cli only
            if ! "${SCRIPT_DIR}/upd_ever-cli.sh"; then
                log_error_stack "Update CLI to new release failed"
                exit 1
            fi
            ;;
        *)
            echo "###-ERROR: Unknown RUN_MODE: $RUN_MODE"
            exit 1
            ;;
    esac
else
    echo "###-ALARM: Node update is DISABLED. Your node may harm the network."
    Send_msg_toTelBot "$VALIDATOR_NAME Server" "$Tg_SOS_sign ###-ALARM: Node update is DISABLED. Your node may harm the network." > /dev/null 2>&1 
fi

######################
if ! "${SCRIPT_DIR}/PostUpdate_Actions.sh"; then
    echoerr "###-ERROR(${SelfScriptName} line $LINENO): PostUpdate_Actions.sh failed!"
    exit 1
fi
######################

echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "================================================================================================"

exit 0
