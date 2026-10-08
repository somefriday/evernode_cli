#!/usr/bin/env bash

# (C) Sergey Tyurin  2024-01-26 10:00:00

# Disclaimer
##################################################################################################################
# You running this script/function means you will not blame the author(s)
# if this breaks your stuff. This script/function is provided AS IS without warranty of any kind. 
# Author(s) disclaim all implied warranties including, without limitation, 
# any implied warranties of merchantability or of fitness for a particular purpose. 
# The entire risk arising out of the use or performance of the sample scripts and documentation remains with you.
# In no event shall author(s) be held liable for any damages whatsoever 
# (including, without limitation, damages for loss of business profits, business interruption, 
# loss of business information, or other pecuniary loss) arising out of the use of or inability 
# to use the script or documentation. Neither this script/function, 
# nor any part of it other than those parts that are explicitly copied from others, 
# may be republished without author(s) express written permission. 
# Author(s) retain the right to alter this disclaimer at any time.
##################################################################################################################

echo
echo "################################# Copy DB from other node ######################################"
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

# Determine OS
OS_TYPE=$(uname -s)

# Check node is not running
if [[ "$OS_TYPE" == "Linux" ]]; then
    NODE_ACTIVE=$(systemctl is-active evernode)
elif [[ "$OS_TYPE" == "FreeBSD" ]]; then
    NODE_ACTIVE=$(service evernode status 2>/dev/null | grep -c 'is running')
else
    echo "Unsupported OS"
    exit 1
fi

if [[ "$NODE_ACTIVE" == "active" || "$NODE_ACTIVE" -gt 0 ]]; then
    echo "Node is running. Stop node before rsync and delete old db"
    exit 1
fi

REMOTE_NODE=${1}
# check if the node name is present in ~/.ssh/config
if ! grep -q "Host ${REMOTE_NODE}" ~/.ssh/config; then
    echo "The node name must be present in ~/.ssh/config"
    exit 1
fi

REMOTE_NODE_DB_DIR=${2:-$NODE_DB_DIR}

# Set the owner of the local node db to the current user
# shellcheck disable=SC2046
sudo chown $(id -un):$(id -gn) "$NODE_DB_DIR" -R

# Copy db from remote node to local node
for ((i=1; i <= 5; i++)); do
    echo "---INFO Downloading db from $REMOTE_NODE attempt $i"
    # shellcheck disable=SC2086
    rsync -arz --ignore-errors --delete \
        $REMOTE_NODE:$REMOTE_NODE_DB_DIR/ \
        "$NODE_DB_DIR" \
        --exclude 'catchains/'
done

# Start node
if [[ "$OS_TYPE" == "Linux" ]]; then
    sudo systemctl start evernode
elif [[ "$OS_TYPE" == "FreeBSD" ]]; then
    sudo service evernode start
fi

echo
echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date)"
echo "================================================================================================"

exit 0
