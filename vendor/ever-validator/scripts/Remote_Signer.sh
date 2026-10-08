#!/usr/bin/env bash
# shellcheck disable=SC2031,SC2155

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

####################
readonly SLEEP_TIMEOUT=60
readonly DePoolTik_Payload="te6ccgEBAQEABgAACCiAmCM="
###################
echo
echo "######################################## Signing script ########################################"
SelfScriptName=$(basename "$0")
echo "INFO: $SelfScriptName BEGIN $(date +%s) / $(date)"
Self_PID=$$

#=====================================================
# Check utilities is installed
if ! command -v yq &>/dev/null; then echo "###-ERROR(${SelfScriptName} line $LINENO): 'yq' is not installed. Please install it and run the script again."; exit 1; fi
if ! command -v jq &>/dev/null; then echo "###-ERROR(${SelfScriptName} line $LINENO): 'jq' is not installed. Please install it and run the script again."; exit 1; fi
if ! command -v bc &>/dev/null; then echo "###-ERROR(${SelfScriptName} line $LINENO): 'bc' is not installed. Please install it and run the script again."; exit 1; fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/functions.shinc"

echo -e "$(DispEnvInfo) \n"
echo -e "$(Determine_Current_Network) \n"

#==================================================
# Stop all background signing processes
find "${KEYS_DIR}"/*_Background_Signing.run -type f -delete 2>/dev/null
# wait for all background functions processes exit
echo -n "Wait for all background signing processes to exit ."
while true; do
    PID_LIST="$(pgrep -a Remote_Signer)"
    BG_PIDs=$(echo "$PID_LIST" | grep -v $Self_PID)
    if [[ -z "$BG_PIDs" ]]; then break; fi
    echo -n ".";
    sleep 5 
done
echo " Done."
if [[ $1 == "stop" ]]; then
    echo "Background signing processes are stopped."
    exit 0
fi
#==================================================
# Check if the node is running and set DAPP mode accordingly
if pgrep -x ${NODE_BIN_NAME} > /dev/null; then
    echo "---WARNING(${SelfScriptName} line $LINENO): Node is running. "
    # If the access mode is set to console and the node is not synchronized, prompt the user to switch to dapp mode temporarily
    if Ask_to_switch_to_dapp;then
        echo "Proceeding..."
        export FORCE_USE_DAPP=true
    elif [[ $? -gt 1 ]];then
        echo "Cancelled."
        exit 1
    fi
else
    echo "---WARNING(${SelfScriptName} line $LINENO): Node is NOT running. Set DAPP mode."
    export FORCE_USE_DAPP=true
fi

#==================================================
# Get Elector address, Elector type, and elector boc file in ${ELECTIONS_WORK_DIR}/${Elector_addr##*:}.boc
if result=$(Get_Current_Elector_Type);then
    read -r Elector_addr ELECTOR_TYPE <<< "$result"
else
    echoerr "###-ERROR(${SelfScriptName} line $LINENO): Cannot get Elector type!"
    echo "$result"
    exit 1
fi
export Elector_addr
export ELECTOR_TYPE
echo "---INFO: Elector type: $ELECTOR_TYPE; Elector address: $Elector_addr"

#==================================================
# Read nodes keys dirs in KEYS_DIR
# Get list of node dirs in KEYS_DIR
declare -a NODES_DIR_LIST
read -ra NODES_DIR_LIST <<< "$(find "${KEYS_DIR}/" -mindepth 1 -maxdepth 1 -type d | tr '\n' ' ')"
echo "NODES_DIR_LIST = ${NODES_DIR_LIST[*]}"
Nodes_Keys_List_File="${KEYS_DIR}/nodes_keys_list.json"

# Start making JSON structure
JSON_NODES_KEYS_LIST="{
  \"nodes\": ["

# Loop through directories in KEYS_DIR
first=1
for ((node_i = 0; node_i < ${#NODES_DIR_LIST[*]}; node_i++)); do
    node_dir="${NODES_DIR_LIST[$node_i]}"
    echo -e "\n------------ Processing node: $node_dir ------------"
    node_name=$(basename "$node_dir")
    # Get msig address for current node
    # check if node has .addr file same as directory name
    if [[ -f "${node_dir}/${node_name}.addr" ]]; then
        msig_addr=$(cat "${node_dir}/${node_name}.addr")
    else
        # Search for msig address file in directory
        read -ra msig_addr_file <<< "$(find "$node_dir" -maxdepth 1 -type f -name "*.addr" ! -regex ".*\(proxy\|depool\|Tik\).*")"
        if [[ ${#msig_addr_file[*]} -eq 1 ]]; then
            msig_addr=$(cat "${msig_addr_file[0]}")
        else
            echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find ${node_name}.addr file in $node_dir"
            exit 1
        fi
        node_name=$(basename "${msig_addr_file[0]}" .addr)
    fi
    echo "---msig_addr = $msig_addr"
    # Check if msig address is active
    MSIG_Info="$(Get_Account_Info "$msig_addr")"
    MSIG_Acc_State="$(echo "$MSIG_Info" |awk '{print $1}')"
    echo "MSIG_Acc_State = $MSIG_Acc_State"
    if [[ "$MSIG_Acc_State" != "Active" ]]; then
        echo "---WARNING(${SelfScriptName} line $LINENO): MSIG address $msig_addr is not active. Set it to empty."
        msig_addr=""
    fi
    
    # Get keys files for current node msig
    read -ra keys_files <<< "$(find "$node_dir" -maxdepth 1 -type f -name "${node_name}_*.keys.json" | tr '\n' ' ')"
    echo "keys_files count = ${#keys_files[@]}"

    # Get depool and proxy addresses if depool exists and deployed
    Depool_addr=""
    proxy0_addr=""
    proxy1_addr=""
    if [[ -f "$node_dir/depool.addr" ]]; then
        Depool_addr="$(cat "$node_dir"/depool.addr)"
        # Check if depool is deployed
        Depool_Info="$(Get_Account_Info "$Depool_addr")"
        Depool_Acc_State="$(echo "$Depool_Info" |awk '{print $1}')"
        if [[ "$Depool_Acc_State" == "Active" ]];then
            # Check if proxy0 and proxy1 addresses files exist
            if [[ -f "$node_dir/proxy0.addr" ]]; then
                proxy0_addr=$(cat "$node_dir"/proxy0.addr)
                proxy1_addr=$(cat "$node_dir"/proxy1.addr)
            else
                # if proxy0.addr file does not exist, get it from depool info
                Current_Depool_Info="$(Get_DP_Info "$Depool_addr")"
                proxy0_addr=$(echo "$Current_Depool_Info" | jq -r ".proxies[0]" | tee "$node_dir/proxy0.addr")
                proxy0_addr=$(echo "$Current_Depool_Info" | jq -r ".proxies[1]" | tee "$node_dir/proxy1.addr")
            fi
        else
            Depool_addr=""
        fi
    fi

    # Comma handling for JSON array elements
    if [[ $first -eq 0 ]]; then
        JSON_NODES_KEYS_LIST+=","
    fi
    first=0

    # Build JSON for current node
    JSON_NODES_KEYS_LIST+="{\"name\": \"$node_name\","
    JSON_NODES_KEYS_LIST+=" \"msig_addr\": \"$msig_addr\","
    JSON_NODES_KEYS_LIST+=" \"depool_addr\": \"$Depool_addr\","
    JSON_NODES_KEYS_LIST+=" \"proxy0_addr\": \"$proxy0_addr\","
    JSON_NODES_KEYS_LIST+=" \"proxy1_addr\": \"$proxy1_addr\","
    JSON_NODES_KEYS_LIST+=" \"keys_files\": ["

    # Add keys files to JSON
    keys_first=1
    for ((key_i = 0; key_i < ${#keys_files[@]}; key_i++)); do
        echo "keys_files[$key_i] = ${keys_files[$key_i]}"
        [[ $keys_first -eq 0 ]] && JSON_NODES_KEYS_LIST+=","
        keys_first=0
        # Absolute path to keys file
        JSON_NODES_KEYS_LIST+="\"${keys_files[$key_i]}\""
    done
    JSON_NODES_KEYS_LIST+="]}"
done

# Close JSON structure
JSON_NODES_KEYS_LIST+="]}"

echo "$JSON_NODES_KEYS_LIST" | jq > "${Nodes_Keys_List_File}"

echo -e "\n========================== Nodes dirs parsing result ====================================="
jq '.' "${Nodes_Keys_List_File}"
echo "==========================================================================================="
echo -e "\nNodes JSON list is saved to ${Nodes_Keys_List_File}\n"
#==================================================
# Sign all transactions in msig allowed for signing
function Sing_All_Msig_Allowed_Transactions() {
    local Node_INFO_JSON="$1"
    local Node_Name=$(echo "$Node_INFO_JSON" | jq -r ".name")
    local Background_Signing_runfile="${KEYS_DIR}/${Node_Name}_Background_Signing.run"
    if [[ -f "$Background_Signing_runfile" ]]; then
        echoerr "###-ERROR(${SelfScriptName} line $LINENO): Background signing already running for $Node_Name. Exit."
        return 1
    fi
    touch "$Background_Signing_runfile"
    local MSIG_ADDR=$(echo "$Node_INFO_JSON" | jq -r ".msig_addr")
    local Depool_addr=$(echo "$Node_INFO_JSON" | jq -r ".depool_addr")
    local DP_hash="${Depool_addr##*:}"
    local proxy0_addr=$(echo "$Node_INFO_JSON" | jq -r ".proxy0_addr")
    local proxy1_addr=$(echo "$Node_INFO_JSON" | jq -r ".proxy1_addr")
    local Trans_List
    local Trans_ID
    local -i  Trans_QTY
    while [[ -f "$Background_Signing_runfile" ]]; do
        # check runfile and exit if it is deleted
        if [[ ! -f "$Background_Signing_runfile" ]]; then
            echo "---INFO: Background signing for $Node_Name is stopped."
            break
        fi
        # Get list of transactions in msig
        Trans_List="$(Get_MSIG_Trans_List "${MSIG_ADDR}")"
        Trans_QTY=$(echo "$Trans_List" | jq -r ".transactions|length")
        if [[ $Trans_QTY -eq 0 ]]; then
            sleep $SLEEP_TIMEOUT
            continue
        fi
        echo -e "\n==== Current time: $(date +'%F %T %Z') ===="
        echo "---INFO: Found $Trans_QTY transactions in msig $MSIG_ADDR for node $Node_Name"
        for ((tx_i=0; tx_i < Trans_QTY; tx_i++)); do
            Trans_ID=$(echo "$Trans_List" | jq -r ".transactions[$tx_i].id")
            Destination_Address=$(echo "$Trans_List" | jq -r ".transactions[$tx_i].dest")
            echo "INFO: Check transaction $Trans_ID to $Destination_Address"
          # Check allowed conditions for signing
            Allowed_to_sign=false
          # 1. destination is elector contract
            if [[ "$Destination_Address" == "$Elector_addr" ]]; then
                echo "INFO: Transaction $Trans_ID is for Elector contract"
                Allowed_to_sign=true
            fi
            # Check depool transaction if it is not empty
            if  [[ ${#DP_hash} -eq 64 ]]; then
              # 2. destination is depool address and payload is tik transaction
                if  [[ $(echo "$Trans_List" | jq -r ".transactions[$tx_i].dest") == "${Depool_addr}" ]] && \
                    [[ $(echo "$Trans_List" | jq -r ".transactions[$tx_i].payload") == "${DePoolTik_Payload}" ]]; then
                    echo "INFO: Transaction $Trans_ID is for DePool Tik transaction"
                    Allowed_to_sign=true
                fi
              # 3. destination is depool address and value is not more 50 tokens
                if [[ $(echo "$Trans_List" | jq -r ".transactions[$tx_i].dest") == "${Depool_addr}" ]] && \
                    [[ $(echo "$Trans_List" | jq -r ".transactions[$tx_i].value") -le 50000000000 ]]; then
                    echo "INFO: Transaction $Trans_ID is for DePool transaction with value $(echo "$Trans_List" | jq -r ".transactions[$tx_i].value")"
                    Allowed_to_sign=true
                fi
              # 4. destination is proxy0 or proxy1 address and value it not more 5 tokens
                if [[ "$Destination_Address" == "$proxy0_addr" ]] || [[ "$Destination_Address" == "$proxy1_addr" ]]; then
                    if [[ $(echo "$Trans_List" | jq -r ".transactions[$tx_i].value") -le 5000000000 ]]; then 
                        echo "INFO: Transaction $Trans_ID is for Proxy transaction with value $(echo "$Trans_List" | jq -r ".transactions[$tx_i].value")"
                        Allowed_to_sign=true
                    fi
                fi
            fi
            if $Allowed_to_sign; then
                KeyFiles_List=$(echo "$Node_INFO_JSON" | jq -r ".keys_files[]")
                for KeyFile in $KeyFiles_List; do
                    echo "Sign transaction $Trans_ID by key file $KeyFile"
                    Send_MSIG_Trans_Confirmation "${Trans_ID}" "${MSIG_ADDR}" "${KeyFile}" &
                    sleep 2
                done
            fi
            sleep 20
        done
    done
}

#==================================================
# Start running background signing for all nodes
Active_Nodes_QTY=$(jq -r ".nodes|length" "${Nodes_Keys_List_File}")
for ((node_i = 0; node_i < Active_Nodes_QTY; node_i++)); do
    Node_INFO_JSON=$(jq ".nodes[$node_i]" "${Nodes_Keys_List_File}")
    # Check if msig address is not empty
    Node_Name=$(echo "$Node_INFO_JSON" | jq -r ".name")
    if [[ -z "$(echo "$Node_INFO_JSON" | jq -r ".msig_addr")" ]]; then
        echo "---WARNING(${SelfScriptName} line $LINENO): Node $Node_Name MSIG address is empty due to not active state. Skip node."
        continue
    fi
    echo "---INFO: Start background signing process for node $(echo "$Node_INFO_JSON" | jq -r ".name")"
    Sing_All_Msig_Allowed_Transactions "$Node_INFO_JSON" &>> "${VALIDATOR_LOG_DIR}/${Node_Name}_signing.log" &
done

exit 0
