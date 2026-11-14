#!/usr/bin/env bash
# shellcheck source=scripts/env.sh

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "${SCRIPT_DIR}/env.sh"

echo
echo "################################# All networks configs update script ###################################"
echo "+++INFO: $(basename "$0") BEGIN $(date +%s) / $(date)"

# Ensure necessary environment variables are set
if [[ -z "$NETWORK_TYPE" || -z "$NET_GLOBAL_CFG_FILE_NAME" || -z "$CONFIGS_DIR" || -z "$NODE_CFG_DIR" ]]; then
    echo "###-ERROR: Required environment variables are not set."
    exit 1
fi

echo "Current network is $NETWORK_TYPE"

MAIN_GLB_URL="https://raw.githubusercontent.com/tonlabs/main.ton.dev/master/configs/$NET_GLOBAL_CFG_FILE_NAME"
NET_GLB_URL="https://raw.githubusercontent.com/tonlabs/net.ton.dev/master/configs/$NET_GLOBAL_CFG_FILE_NAME"

MAIN_CFG_DIR="$CONFIGS_DIR/main.ton.dev"
NET_CFG_DIR="$CONFIGS_DIR/net.ton.dev"

mkdir -p "$HOME/logs"

declare -a net_list=("$MAIN_CFG_DIR" "$NET_CFG_DIR")
declare -a g_url_list=("$MAIN_GLB_URL" "$NET_GLB_URL")
declare -a dir_list=("$MAIN_CFG_DIR" "$NET_CFG_DIR")

for i in "${!g_url_list[@]}"; do
    mkdir -p "${dir_list[i]}"
    echo -n "Update global config for ${net_list[i]##*/} network... "
    curl -o "${dir_list[i]}/$NET_GLOBAL_CFG_FILE_NAME" "${g_url_list[i]}" &>/dev/null
    if [[ $? -eq 0 ]]; then
        echo " ..DONE"
    else
        echo " ..FAILED"
    fi
done

ParentScript="$(ps -o command= $PPID | awk -F'/' '{print $NF}')"
if [[ "${ParentScript}" != "Setup.sh" ]]; then
    cp -f "${CONFIGS_DIR}/${NETWORK_TYPE}/$NET_GLOBAL_CFG_FILE_NAME" "${NODE_CFG_DIR}/"
fi

echo
echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date)"
echo "================================================================================================"

exit 0
