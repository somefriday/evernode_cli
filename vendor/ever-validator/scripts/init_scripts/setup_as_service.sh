#!/usr/bin/env bash
# shellcheck source=../env.sh

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
echo "################################# service setup script ###################################"
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

SVC_TMP_FILE="$(mktemp -t -p "${EVER_TMP_DIR}" evernode.service.XXXXXX)"
OS_SYSTEM=$(uname -s)
if [[ "${OS_SYSTEM}" == "Linux" ]];then
    ########################################################################
    ########### Node Services for Linux (Ubuntu, CentOS & Oracle) ##########
    SERVICE_FILE="/etc/systemd/system/evernode.service"
    # SERVICE_FILE="/usr/lib/systemd/system/evernode.service"
    #=====================================================
    # Rust node on Linux ##
SVC_FILE_CONTENTS=$(cat <<-_EOF_
[Unit]
Description=Everscale Validator RUST Node
After=network.target
StartLimitIntervalSec=0
[Service]
Environment="STATSD_DOMAIN=127.0.0.1"
Environment="STATSD_PORT=9125"
Environment="PROMETHEUS_PORT=9090"
Type=simple
Restart=always
RestartSec=5
TimeoutStopSec=600
User=$USER
LimitNOFILE=2048000
ExecStart=$CALL_NODE
[Install]
WantedBy=multi-user.target
_EOF_
)
    echo "${SVC_FILE_CONTENTS}" > "${SVC_TMP_FILE}"
    sudo mv -f "${SVC_TMP_FILE}" "${SERVICE_FILE}"
    sudo chown root:root "${SERVICE_FILE}"
    sudo chmod 644 "${SERVICE_FILE}"
    Linux_Distrib="$(hostnamectl |grep 'Operating System'|awk '{print $3}')"
    if  [[ "${Linux_Distrib}" == "CentOS" ]] || \
        [[ "${Linux_Distrib}" == "Oracle" ]] || \
        [[ "${Linux_Distrib}" == "Red" ]] || \
        [[ "${Linux_Distrib}" == "Rocky" ]] || \
        [[ "${Linux_Distrib}" == "Fedora" ]]; then
        # ll -Z /etc/systemd/system
        sudo chcon system_u:object_r:rnode_exec_t:s0 "${SERVICE_FILE}"
    elif [[ "${Linux_Distrib}" == "Ubuntu" ]]; then
        if [[ ! -f /etc/needrestart/conf.d/evernode.conf ]]; then
            # shellcheck disable=SC2016
            echo '$nrconf{override_rc}{qr(^evernode\.service$)} = 0;' | sudo tee /etc/needrestart/conf.d/evernode.conf
        fi
    fi
    sudo systemctl daemon-reload
    sudo systemctl enable evernode

    # shellcheck disable=SC2154
    echo -e "\nTo start node service run ${BoldText}${GreenBack}sudo service evernode start${NormText}"
    echo "To restart updated node or service - run all follow commands:"
    echo
    echo "sudo systemctl disable evernode"
    echo "sudo systemctl daemon-reload"
    echo "sudo systemctl enable evernode"
    echo "sudo service evernode restart"

else   #  -------------------- OS select
    # Next  for FreeBSD
    ########################################################################
    ############## FreeBSD rc daemon ########################################
    echo "---INFO: Setup rc daemon..."
    SERVICE_FILE="/usr/local/etc/rc.d/evernode"
    sed -e "s%N_LOG_DIR%${NODE_LOGS_ARCH}%" \
        -e "s%N_SERVICE_DESCRIPTION%Everscale RUST Node Daemon%" \
        -e "s%N_USER%${USER}%g" \
        -e "s%N_NODE_LOGS_ARCH%${NODE_LOGS_ARCH}%g" \
        -e "s%N_NODE_LOG_FILE%${NODE_LOG_DIR}/${NODE_LOG_FILE}%g" \
        -e "s%N_NODE_STDERR_LOG_FILE%${NODE_LOG_DIR}/stderr.log%g" \
        -e "s%N_NODE_STDOUT_LOG_FILE%${NODE_LOG_DIR}/stdout.log%g" \
        -e "s%N_COMMAND%$CALL_NODE%" \
        -e "s%NODE_BIN_NAME%${NODE_BIN_NAME}%g" \
        -e "s%N_ARGUMENTS% %" "${CONFIGS_DIR}/rnode/FB_service.tmplt" > "${SVC_TMP_FILE}"
    ########################################################################

    sudo mv -f "${SVC_TMP_FILE}" "${SERVICE_FILE}"
    sudo chown root:wheel "${SERVICE_FILE}"
    sudo chmod 755 "${SERVICE_FILE}"
    sudo sysrc evernode_enable="YES"
    ls -al "${SERVICE_FILE}"

    echo -e "To start node service run ${BoldText}${GreenBack}'service evernode start'${NormText}"
    echo "To restart updated node or service run 'service evernode restart'"
    echo

    echo "---INFO: rc daemon setup DONE!"
fi   # ############################## OS select

echo
echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date)"
echo "================================================================================================"

exit 0
