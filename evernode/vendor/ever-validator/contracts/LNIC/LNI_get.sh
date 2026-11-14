#!/usr/bin/env bash
# shellcheck source=../scripts/text_mods.shinc
# shellcheck source=../scripts/env.sh
set -eE

# (C) Sergey Tyurin  2024-10-10 10:00:00

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

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
source "${SCRIPT_DIR}/../scripts/env.sh"
source "${SCRIPT_DIR}/../scripts/functions.shinc"

#=================================================
echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo

#=================================================
# Get LNIC account state
echo "--- Get LNIC account state ---"
if ! LNIC_State="$(Get_Account_Info "$LNIC_ADDRESS")";then
    echo "###-ERROR(line $LINENO): LNIC account not found. Can't continue. Sorry."
    exit 1
else
	if [[ "$(echo "$LNIC_State"|awk '{print $1}')" != "Active" ]];then
		echo "###-ERROR(line $LINENO): LNIC account is not active. Can't continue. Sorry."
		exit 1
	fi
	echo "--- INFO: LNIC account is active"
    if ! OUTPUT="$(Get_SC_current_state "$LNIC_ADDRESS")";then
        echo "###-ERROR(line $LINENO): Cannot get LNIC account state. Can't continue. Sorry."
        exit 1
    else
        echo "--- INFO: LNIC state saved to ${ELECTIONS_WORK_DIR}/${LNIC_ADDRESS##*:}.boc"
		echo "--- INFO: LNIC current balance: $(echo "$LNIC_State"|awk '{printf "%.3f\n", $2/1000000000}')"
    fi
fi

# Get LNIC info
echo "--- INFO: Get LNIC info from account $LNIC_ADDRESS"
Curr_LNI_Info="$(get_LastNodeInfo)"
jq . <<< "$Curr_LNI_Info"

exit 0
