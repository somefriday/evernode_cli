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

echo
echo "################################# logrotate setup script ###################################"
SelfScriptName=$(basename "$0")
echo "--- INFO: ${SelfScriptName} BEGIN $(date +%s) / $(date  +'%F %T %Z')"
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

command -v logrotate >/dev/null 2>&1 || { echo >&2 "###-ERROR(${SelfScriptName} line $LINENO): logrotate is required but it's not installed. Aborting."; exit 1; }

#============================================
# setup log rotate file
NODES_LOG_ROT=$(cat <<-_ENDNLR_
${NODE_LOG_DIR}/stderr.log
${NODE_LOG_DIR}/stdout.log
${VALIDATOR_LOG_DIR}/${VALIDATOR_LOG_FILE_NAME}
${VALIDATOR_LOG_DIR}/transactions.log
{
    rotate 4
    weekly
    missingok
    notifempty
    maxsize 1G
    copytruncate
    compress
    dateext
    dateyesterday
    postrotate
        DIRECTORY=$ELECTIONS_WORK_DIR
        # Folder to archive elections directories
        ARCHIVE_DIR=\$DIRECTORY/elections_hist
        # Find elections directories older than 30 days and archive them
        find "\$DIRECTORY" -maxdepth 1 -mindepth 1 -type d -mtime +30 -exec sh -c '
            for dir do
                # Make archive
                tar -czf "\$ARCHIVE_DIR/$(basename "\$dir").tgz" -C "\$dir" .
                # Remove original directory
                rm -rf "\$dir"
            done
        ' sh {} +
    endscript
}

_ENDNLR_
)

echo "$NODES_LOG_ROT" > "${CONFIGS_DIR}/rot_nodelog.cfg"
OS_SYSTEM=$(uname -s)
#==============================================================================
if [[ "$OS_SYSTEM" == "Linux" ]];then
# Ubuntu, CentOS & Oracle
    Linux_Distrib="$(hostnamectl |grep 'Operating System'|awk '{print $3}')"
    LOGROT_FILE="/etc/logrotate.d/evernode"
    Root_UN="$(id -un root)"
    Root_GN="$(id -gn root)"
    sudo cp -f "${CONFIGS_DIR}/rot_nodelog.cfg" "${LOGROT_FILE}"
    sudo chown "${Root_UN}":"${Root_GN}" "${LOGROT_FILE}"
    sudo chmod 644 "${LOGROT_FILE}"
    if [[ "${Linux_Distrib}" == "CentOS" ]] || \
       [[ "${Linux_Distrib}" == "Oracle" ]] || \
       [[ "${Linux_Distrib}" == "Red" ]];then
        # ll -Z /etc/systemd/system
        sudo chcon system_u:object_r:etc_t:s0 "${LOGROT_FILE}"
    fi
Run_Script=$(cat <<-_ENDNLR_
#!/bin/sh

/usr/sbin/logrotate -l ${NODE_LOGS_ARCH}/logrotate.log -s ${NODE_LOGS_ARCH}/logrotate.status -f /etc/logrotate.conf
EXITVALUE=\$?
if [ \$EXITVALUE != 0 ]; then
    /usr/bin/logger -t logrotate "ALERT exited abnormally with [\$EXITVALUE]"
fi
exit \$EXITVALUE

_ENDNLR_
)
    Cron_Run_File="/etc/cron.daily/logrotate"
    tmpfile=$(mktemp -t logrotate.XXXX -p "${EVER_TMP_DIR}")
    echo "$Run_Script" > "$tmpfile"
    sudo mv -f "$tmpfile" ${Cron_Run_File}
    sudo chown "${Root_UN}":"${Root_GN}" ${Cron_Run_File}
    sudo chmod 755 ${Cron_Run_File}
    if [[ "${Linux_Distrib}" == "CentOS" ]] || \
       [[ "${Linux_Distrib}" == "Oracle" ]] || \
       [[ "${Linux_Distrib}" == "Red" ]];then
        # ll -Z /etc/systemd/system
        sudo chcon system_u:object_r:etc_t:s0 ${Cron_Run_File}
    fi
else
#==============================================================================
# FreeBSD
    LOGROT_FILE="/usr/local/etc/logrotate.d/evernode"
    Root_UN="$(id -un root)"
    Root_GN="$(id -gn root)"
    sudo cp -f "${CONFIGS_DIR}/rot_nodelog.cfg" "${LOGROT_FILE}"
    sudo chown "${Root_UN}":"${Root_GN}" "${LOGROT_FILE}"
    sudo chmod 644 "${LOGROT_FILE}"

Run_Script=$(cat <<-_ENDNLR_
#!/bin/sh

/usr/local/sbin/logrotate -l ${NODE_LOGS_ARCH}/logrotate.log -s ${NODE_LOGS_ARCH}/logrotate.status -f /usr/local/etc/logrotate.conf
EXITVALUE=\$?
if [ \$EXITVALUE != 0 ]; then
    /usr/bin/logger -t logrotate "ALERT exited abnormally with [\$EXITVALUE]"
fi
exit \$EXITVALUE

_ENDNLR_
)
    Cron_Run_File="/usr/local/etc/periodic/daily/logrotate"
    tmpfile=$(mktemp -t logrotate.XXXX -p "${EVER_TMP_DIR}")
    echo "$Run_Script" > "$tmpfile"
    sudo mv -f "$tmpfile" ${Cron_Run_File}

    sudo chown "${Root_UN}":"${Root_GN}" ${Cron_Run_File}
    sudo chmod 755 ${Cron_Run_File}
    sudo touch /var/log/lastlog
    sudo chmod 644 /var/log/lastlog
fi
#===========================

echo "---INFO: Logrotate file created:"
ls -lhFp "${LOGROT_FILE}"

echo
echo "+++INFO: ${SelfScriptName} FINISHED $(date +%s) / $(date)"
echo "================================================================================================"

exit 0
