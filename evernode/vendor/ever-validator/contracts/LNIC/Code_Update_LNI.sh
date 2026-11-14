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

Contract_Name="LastNodeInfo"
CALL_SOLD=sold76
Contract_TVC="${Contract_Name}.tvc"
Contract_ABI="${Contract_Name}.abi.json"

KEYS_FILE="${Contract_Name}.keys.json"
ADDR_FILE="${Contract_Name}.addr"
LNIC_ADDRESS=$(cat "${ADDR_FILE}")
UPDATE_FILE="update_code.json"
DATA_FILE="update_data.json"

# Compile contract
# sold64  sold66  sold67  sold72  sold73 sold75 sold76
echo "###-INFO: Compile contract with $CALL_SOLD"
if ! $CALL_SOLD ${Contract_Name}.sol;then
	echo "###-ERROR: Cannot compile contract!!!"
	exit 1
fi
cp -f ${Contract_TVC} ${Contract_TVC}.compiled
# Initilize code before update
# Note: For contracts using ABI 2.4, it is necessary to first insert the deployment public key into the TCV file. This can be achieved using the genaddr function.
# Note: If your contract has static variables, they can be initialized with genaddr command before deployment.
# ever-cli genaddr [--genkey|--setkey <keyfile.json>] [--wc <int8>] [--abi <contract.abi.json>] [--save] [--data <data>] <contract.tvc>
Contract_data="$(jq -c . ${DATA_FILE})"
if ! OUTPUT="$($CALL_CLI genaddr --setkey ${KEYS_FILE} --wc 0 --abi ${Contract_ABI} --save ${Contract_TVC})"; then #--data "${InitialData}"
	echo "###-ERROR(line $LINENO): Cannot initilize contract's TVC by genaddr command"
	echo "Output: $OUTPUT"
	exit 1
else
	echo -e "--- INFO: Contract was initialized by genaddr\n$OUTPUT"
fi

# Prepare update data
cat <<_ENDCNT_ > $UPDATE_FILE
{
  "newcode": "xxx",
  "new_code_time": "xx",
  "new_ABI": "xxx"
}
_ENDCNT_

# Get new code
echo "--- Get new contract's code ---"
if ! newcode="$($CALL_CLI -j decode stateinit --tvc ${Contract_TVC} | jq -r '.code')"; then
	echo "Failed to get code"
	exit 1
fi
new_code_time="$(date +%s)"

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
# Compacting ABI json file compressing it with xz and converting to hex
echo "--- Pack ABI json file ---"
rm -f ${Contract_Name}.abi.json.xz |cat > /dev/null
rm -f ${Contract_Name}.abi.json.7z |cat > /dev/null
jq -c . ${Contract_Name}.abi.json > "${Contract_Name}.abi.json.tmp" && mv -f "${Contract_Name}.abi.json.tmp" ${Contract_Name}.abi.json
xz -z -k -9 -e -T0 ${Contract_Name}.abi.json && xxd -ps ${Contract_Name}.abi.json.xz|tr -d '\n' > ${Contract_Name}.abi.hex
# 7za a -m0=ppmd ${Contract_Name}.abi.json.7z ${Contract_Name}.abi.json && xxd -ps ${Contract_Name}.abi.json.7z|tr -d '\n' > ${Contract_Name}.abi.hex
ABI_HEX="$(cat ${Contract_Name}.abi.hex)"

jq -c ".newcode = \"${newcode}\" | .new_code_time = ${new_code_time} | .new_ABI = \"${ABI_HEX}\"" "${UPDATE_FILE}" > "${UPDATE_FILE}.tmp"
mv -f "${UPDATE_FILE}.tmp" "${UPDATE_FILE}"
jq . "${UPDATE_FILE}"


# Prepare init data
echo "--- Prepare init data ---"
NewData="$(jq -c .new_node_info ${DATA_FILE})"
InitialData="$(echo '{}' | jq -c ".initial_node_info = ${NewData} | \
	.code_deploy_time = ${new_code_time} | \
	.info_deploy_time = ${new_code_time} | \
	.initial_ABI = \"${ABI_HEX}\"" )"
echo "InitialData: $InitialData"

# ====================================================
read -rp "### CHECK INFO TWICE!!! Is this a right update?  (y/n)? " </dev/tty answer
case ${answer:0:1} in
    y|Y|yes|Yes|YES )
        echo "Processing....."
    ;;
    * )
        echo "Cancelled."
        exit 1
    ;;
esac
# ====================================================

# Update contract code
echo "--- Update contract code ---"
set -x
if ! $CALL_CLI call --abi ${Contract_ABI} --sign ${KEYS_FILE} "$(cat ${ADDR_FILE})" updateContractCode ${UPDATE_FILE}; then
	echo "Failed to update contract code"
	exit 1
fi
set +x
# "{\"newcode\": \"$NewCode\", \"new_ABI\": \"$NewABI\"}"

# restore node info after update code
echo "--- Restore node info ---"
new_info_time="$(date +%s)"
jq ".new_info_time = ${new_info_time}" "${DATA_FILE}" > "${DATA_FILE}.tmp"
mv -f "${DATA_FILE}.tmp" "${DATA_FILE}"
$CALL_CLI call --abi ${Contract_ABI} --sign ${KEYS_FILE} "$(cat ${ADDR_FILE})" change_node_info ${DATA_FILE}

exit 0

# ever-cli -j run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) getALLinfo {}
# ever-cli -j run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) getLastNodeInfo {}
# ever-cli -j run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) node_info {}
# ever-cli -j run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) code_ver {}
# ever-cli -j run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) code_updated_time {}
# ever-cli -j run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) info_updated_time {}
# ever-cli -j run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) ABI {}
# ever-cli -j run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) ABI {}|jq -r '.ABI'|xxd -r -p > lnm.7z
# ever-cli -j run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) getABI {}
# ever-cli -j run --abi ${Contract_Name}.abi.json $(cat ${Contract_Name}.addr) getABI {}|jq -r '.value0'|xxd -r -p > lnm.7z
