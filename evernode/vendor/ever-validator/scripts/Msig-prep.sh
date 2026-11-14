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
echo "######################### Generate multisig wallet address and keys ############################"
SelfScriptName=$(basename "$0")
echo "---INFO: ${SelfScriptName} BEGIN $(date +%s) / $(date  +'%F %T %Z')"

function show_usage(){
echo
echo " Use: ./${SelfScriptName} <Wallet name> <'Safe' or 'SetCode'> <Num of custodians> [<workchain>]"
echo " All fields required!"
echo "<Wallet Name> - name of wallet. use \$VALIDATOR_NAME for validator wallet or '--' for auto name"
echo "<'Safe' or 'SetCode'> - SafeCode or SetCode multisig wallet"
echo "<num of custodians> must greater 0 or less 32"
echo "<workchain> - workchain to deploy wallet. NODE_WC or '-1' "
echo "For MSIG validation mode you must use '-1' workchain!"
echo "For DEPOOL validation mode use wallet workchain NODE_WC to minimize fees"
echo
echo "All files will be saved in $KEY_FILES_DIR"
echo "if you have file '<Wallet name>_1.keys.json' with seed phrase in this dir - it will used to generate address"
echo "if you have such files (..._2..., ..._3... etc) for each custodian, it will use for key pairs generation respectively"
echo
echo " Example: ./${SelfScriptName} -- Safe 3 0"
echo
exit 1
}

[[ $# -lt 3 ]] && show_usage

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

WAL_NAME=$1
if [[ "$WAL_NAME" == "--" ]];then WAL_NAME=$VALIDATOR_NAME; fi
if [[ "$WAL_NAME" != "$VALIDATOR_NAME" ]];then
    echo "###-WARNING(${SelfScriptName} line $LINENO): Wallet name is not equal to VALIDATOR_NAME in env.sh"
fi

CodeOfWallet=$2
if [[ ! $CodeOfWallet == "Safe" ]] && [[ ! $CodeOfWallet == "SetCode" ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Wrong code of wallet. Choose 'Safe' or 'SetCode'"
    show_usage
    exit 1
fi
CUSTODIANS=$3
if [[ $CUSTODIANS -lt 1 ]] || [[ $CUSTODIANS -gt 32 ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Wrong Num of custodians must be >= 1 and <= 31"  
    show_usage
    exit 1
fi
WorkChain=${4:-0}
if [[ "$WorkChain" != "-1" ]] && [[ "$WorkChain" != "$NODE_WC" ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Wrong '$WorkChain' workchain. Choose '-1' or same as NODE_WC in env.sh - $NODE_WC"
    show_usage
    exit 1
fi

KEY_FILES_DIR="$KEYS_DIR/MSKeys_${WAL_NAME}"
[[ ! -d $KEY_FILES_DIR ]] && mkdir -p "$KEY_FILES_DIR"

# JSON file with all addresses and keys
Node_Addr_Keys_File="${KEYS_DIR}/${VALIDATOR_NAME}.secret.json"
# if file is not exist - create it
if [[ ! -f $Node_Addr_Keys_File ]];then
   jq -n --arg vn "$VALIDATOR_NAME" \
    '{
    nodes: [
      {($vn): {
        msig_addr: "",
        depool_addr: "",
        proxy0_addr: "",
        proxy1_addr: "",
        msig_keys_mask: ($vn + "_*.keys.json"),
      }}
    ]
  }' > "$Node_Addr_Keys_File"
fi
#=======================================================================================
# Set wallet code and ABI
Wallet_Code="${INPL_SafeSCs_DIR}/SafeMultisigWallet.tvc"
Wallet_ABI="${INPL_SafeSCs_DIR}/SafeMultisigWallet.abi.json"
if [[ "$CodeOfWallet" == "SetCode" ]];then
    Wallet_Code="${INPL_SetSCs_DIR}/SetcodeMultisigWallet.tvc"
    Wallet_ABI="${INPL_SetSCs_DIR}/SetcodeMultisigWallet.abi.json"
fi

echo "Wallet Code: $Wallet_Code"
echo "ABI for wallet: $Wallet_ABI"

#=======================================================================================
# generation cycle
declare -a SeedPhrase
for (( i=1; i <= $((CUSTODIANS)); i++ )); do
    echo "$i"
    
    # generate or read seed phrases
    [[ ! -f ${KEY_FILES_DIR}/${WAL_NAME}_seed_${i}.txt ]] && SeedPhrase[$i]=$($CALL_CLI genphrase | grep "Seed phrase:" | cut -d' ' -f3-14 | tee "${KEY_FILES_DIR}/${WAL_NAME}_seed_${i}.txt")
    [[ -f ${KEY_FILES_DIR}/${WAL_NAME}_seed_${i}.txt ]] && SeedPhrase[$i]=$(cat "${KEY_FILES_DIR}/${WAL_NAME}_seed_${i}.txt")
    SeedPhrase[$i]=$(echo "${SeedPhrase[$i]}" | tr -d '"')
    
    # generate public key
    PubKey=$($CALL_CLI genpubkey "${SeedPhrase[$i]}" | \
        tee "${KEY_FILES_DIR}/${WAL_NAME}_PubKeyCard_${i}.txt" | \
        grep "Public key:" | awk '{print $3}' | \
        tee "${KEY_FILES_DIR}/${WAL_NAME}_pub_${i}_.key")
    echo "PubKey${i}: $PubKey"
    
    # generate pub/sec keypair file
    if ! KEY_PAIR_JSON="$($CALL_CLI -j getkeypair -p "${SeedPhrase[$i]}")"; then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Can't generate key pair for seed phrase ${SeedPhrase[$i]}"
        exit 1
    fi
    if ! echo "$KEY_PAIR_JSON"|jq > "${KEY_FILES_DIR}/${WAL_NAME}_${i}.keys.json"; then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Can't save key pair to file ${KEY_FILES_DIR}/${WAL_NAME}_${i}.keys.json"
        exit 1
    fi
done
# write seed phrases to Node_Addr_Keys_File
json_array=$(printf '%s\n' "${SeedPhrase[@]}" | jq -R . | jq -s .)
yq e -ioj ".nodes[0].${VALIDATOR_NAME}.SeedPhrases = $json_array" "$Node_Addr_Keys_File"
#=======================================================================================
# generate multisignature wallet address
if ! WalletAddress="$($CALL_CLI genaddr "$Wallet_Code" --abi "$Wallet_ABI" --setkey "${SeedPhrase[1]}" --wc "$WorkChain")"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't generate wallet address"
    echo "$WalletAddress"
    echo -e "----------------------------------------------------------------------------------------------------------------------------\n"
    exit 1
fi
WalletAddress="$(echo "$WalletAddress" \
		| tee  "${KEY_FILES_DIR}/${WAL_NAME}_addr-card.txt" \
		| grep "Raw address:" | awk '{print $3}' \
		| tee "${KEY_FILES_DIR}/${WAL_NAME}.addr")"

echo "---INFO(${SelfScriptName} line $LINENO): All files saved in $KEY_FILES_DIR"
echo "---INFO(${SelfScriptName} line $LINENO): Wallet Address: $WalletAddress"

#=======================================================================================
# check and copy files to KEYS_DIR if not exist
if [[ ! -f ${KEYS_DIR}/${WAL_NAME}.addr ]];then
    [[ ! -f "${KEYS_DIR}/${WAL_NAME}.addr" ]] && cp "${KEY_FILES_DIR}/${WAL_NAME}.addr" "${KEYS_DIR}"/
    for (( i=1; i <= $((CUSTODIANS)); i++ )); do
        [[ ! -f "${KEYS_DIR}/${WAL_NAME}_${i}.keys.json" ]] && cp "${KEY_FILES_DIR}/${WAL_NAME}_${i}.keys.json" "${KEYS_DIR}"/
    done
    echo "---INFO(${SelfScriptName} line $LINENO): MSIG files ${WAL_NAME}.addr & ${WAL_NAME}_*.keys.json copied to ${KEYS_DIR}/"
fi

echo
echo "If you want to replace exist wallet, you need to delete all files in ${KEY_FILES_DIR}/ and ${WAL_NAME}.addr & ${WAL_NAME}_*.keys.json in ${KEYS_DIR}/ folder"
echo
echo "To deploy wallet, send tokens to it address and use MS-Wallet_deploy.sh script"
echo
echo -e "${BoldText}${RedBack} Save all seed phrases!! ${NormText} from '${WAL_NAME}_seed_*.txt' files in ${KEY_FILES_DIR}/"
echo

echo "+++INFO: ${SelfScriptName} FINISHED $(date +%s) / $(date)"
echo "================================================================================================"

exit 0
