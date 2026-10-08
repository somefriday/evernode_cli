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
echo "################################# DePool generate address script ############################"
SelfScriptName=$(basename "$0") && export SelfScriptName
echo "INFO: $SelfScriptName BEGIN $(date +%s) / $(date +'%F %T %Z')"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
if ! source "${SCRIPT_DIR}/env.sh"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't load env.sh"
    exit 1
fi
# shellcheck source=functions.shinc
source "${SCRIPT_DIR}/functions.shinc"
echo
echo "Current Time: $(date +'%F %T %Z')"
echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo
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
source "${SCRIPT_DIR}/functions.shinc"

Depool_WC=$NODE_WC
if [[ $Depool_WC -lt 0 ]]; then
    echo "###-WARNING(${SelfScriptName} line $LINENO): DePool workchain can not be equal to -1. Change it to 0"
    Depool_WC=0
fi

OS_SYSTEM=$(uname -s)
if [[ "$OS_SYSTEM" == "Linux" ]];then
        GetMD5="md5sum --tag"
else
        GetMD5="md5"
fi

KEY_FILES_DIR="$KEYS_DIR/DPKeys_${VALIDATOR_NAME}"
[[ ! -d $KEY_FILES_DIR ]] && mkdir -p $KEY_FILES_DIR

#=======================================================================================
# Set DePool code and ABI
Depool_Code=${INPL_DSCs_DIR}/DePool.tvc
Depool_ABI=${INPL_DSCs_DIR}/DePool.abi.json

DePoolMD5=$($GetMD5 "${DSCs_DIR}/DePool.tvc" |awk '{print $4}')
echo "Depool Code: $Depool_Code"
echo "Depool ABI : $Depool_ABI"
echo "Depool MD5 : $DePoolMD5"

#=======================================================================================
# generate files

#----------------------------------------------------------    
# generate or read seed phrases
if [[ ! -f ${KEY_FILES_DIR}/depool_seed.txt ]];then
    SeedPhrase="$($CALL_CLI genphrase | grep "Seed phrase:" | cut -d' ' -f3-14 | tee "${KEY_FILES_DIR}/depool_seed.txt")"
else 
    SeedPhrase="$(cat "${KEY_FILES_DIR}/depool_seed.txt")"
fi
SeedPhrase=$(echo "$SeedPhrase" | tr -d '"')

if [[ -z $SeedPhrase ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can not generate seed phrase."
    echo
    exit 1
fi
echo "Seed phrase saved to ${KEY_FILES_DIR}/depool_seed.txt"

#----------------------------------------------------------    
# generate public key
PubKey="$($CALL_CLI genpubkey "$SeedPhrase" | tee "${KEY_FILES_DIR}/depool_PubKeyCard.txt" | grep "Public key:" | awk '{print $3}' | tee "${KEY_FILES_DIR}/depool_pub.key")"
if [[ -z $PubKey ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can not generate PubKey."
    echo
    exit 1
fi
echo "PubKey: $PubKey"
echo "Public Key saved to ${KEY_FILES_DIR}/depool_pub.txt"

#----------------------------------------------------------    
# generate pub/sec keypair file
if ! KEY_PAIR_JSON="$($CALL_CLI -j getkeypair -p "$SeedPhrase")"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't generate key pair for seed phrase $SeedPhrase"
    exit 1
fi
if ! echo "$KEY_PAIR_JSON"|jq > "${KEY_FILES_DIR}/depool.keys.json"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't save key pair to file ${KEY_FILES_DIR}/depool.keys.json"
    exit 1
fi

key_public=$(jq ".public" "${KEY_FILES_DIR}/depool.keys.json" | tr -d '"')
key_secret=$(jq ".secret" "${KEY_FILES_DIR}/depool.keys.json" | tr -d '"')
if [[ -z $key_public ]] || [[ -z $key_secret ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Error generating keypair file!"
    exit 1
fi
echo "Key pair file saved to ${KEY_FILES_DIR}/depool.keys.json"

Validator_addr="$(cat "${KEYS_DIR}/${VALIDATOR_NAME}.addr")"
[[ -z "$Validator_addr" ]] && echo "###-ERROR(${SelfScriptName} line $LINENO): Validator address not found in ${KEYS_DIR}/${VALIDATOR_NAME}.addr" && exit 1
Validator_WC=${Validator_addr%%:*}
if [[ "$Depool_WC" != "$Validator_WC" ]] && [[ "$Validator_WC" != "-1" ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Validator address WC is not equal Node WC"
    exit 1
fi
if [[ "$Validator_WC" == "-1" ]];then
    echo "###-WARNING(${SelfScriptName} line $LINENO): Validator address WC is -1. Change it to $Depool_WC to avoid extra fees"
fi
#=======================================================================================
# generate depool address
if ! DepoolAddress="$($CALL_CLI genaddr $Depool_Code --abi $Depool_ABI --setkey "${SeedPhrase}" --wc "$Depool_WC")"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't generate DePool address"
    echo "$DepoolAddress"
    echo -e "----------------------------------------------------------------------------------------------------------------------------\n"
    exit 1
fi
DepoolAddress="$(echo "$DepoolAddress" | \
    tee "${KEY_FILES_DIR}/depool_addr-card.txt" | \
    grep "Raw address:" | awk '{print $3}' | \
    tee "${KEY_FILES_DIR}/depool.addr")"

echo "================================================================================================"
echo
echo "All files saved in $KEY_FILES_DIR"
echo
echo "Depool Address: $DepoolAddress"
echo -e "${BoldText}REMEMBER: Depool CRITICAL_THRESHOLD is 10 tokens. If the depool balance is less than 10 tokens, the depool will be stuck and will not be able to operate at all.${NormText}"

if [[ ! -f ${KEYS_DIR}/depool.addr ]];then
    [[ ! -f "${KEYS_DIR}/depool.addr" ]] && cp "${KEY_FILES_DIR}/depool.addr" "${KEYS_DIR}"/
    [[ ! -f "${KEYS_DIR}/depool.keys.json" ]] && cp "${KEY_FILES_DIR}/depool.keys.json" "${KEYS_DIR}"/
    echo "DePool files 'depool.addr' & 'depool.keys.json' copied to ${KEYS_DIR}/"
fi

echo
echo "If you want to replace exist depool, you need to delete all files in ${KEY_FILES_DIR}/ and 'depool.addr' & 'depool.keys.json' in ${KEYS_DIR}/ folder"
echo
echo -e "${BoldText}${RedBack} Save DePool seed phrase!! ${NormText} from 'depool_seed.txt' file in ${KEY_FILES_DIR}/"
echo
echo "To deploy Depool, send 50 tokens to this address and use 'DP5_depool_deploy.sh' script"
echo -e "${BoldText}${RedBack}### NB! ### Do not forget to change Depool parameters in 'env.sh' script${NormText}"
echo
echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date)"
echo "================================================================================================"

exit 0
