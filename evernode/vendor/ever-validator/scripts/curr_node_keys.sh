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

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
if ! source "${SCRIPT_DIR}/env.sh"; then
    echo "###-ERROR: Can't load env.sh"
    exit 1
fi
source "${SCRIPT_DIR}/functions.shinc"

echo
echo -e "$(Determine_Current_Network)"
echo
# =====================================================
Curr_Elec="$(Get_Current_Elections_ID)"
echo "Current Elections ID: $Curr_Elec"
echo

Curr_Engine_Val_Keys=$(jq '.validator_keys' "${NODE_CFG_DIR}/config.json")
# Curr_Engine_Key_Ring=$(jq '.validator_key_ring' "${NODE_CFG_DIR}/config.json")
[[ "$Curr_Engine_Val_Keys" == "null" ]] && echo "No keys found" && exit 0
ADNL_0=$(echo "${Curr_Engine_Val_Keys}" | jq .[0].validator_adnl_key_id)
Elec_0=$(echo "${Curr_Engine_Val_Keys}" | jq .[0].election_id)
ADNL_1=$(echo "${Curr_Engine_Val_Keys}" | jq .[1].validator_adnl_key_id)
Elec_1=$(echo "${Curr_Engine_Val_Keys}" | jq .[1].election_id)
if [[ "$ADNL_0" == "null" ]];then
    echo "No keys found"
    exit 0 
fi
if [[ "$ADNL_1" == "null" ]];then
    Engine_ADNL=$(echo "$ADNL_0" | tr -d '"'|base64 -d|od -t xC -An|tr -d '\n'|tr -d ' ')
    # VAL_KEY_ID="$(cat ${NODE_CFG_DIR}/config.json      | jq -r ".validator_keys[]|select(.election_id == $Elec_0)|.validator_key_id")"
    Engine_KEY_ID=$(echo "$Curr_Engine_Val_Keys"       | jq -r ".[]|select(.election_id == $Elec_0)|.validator_key_id")
    # Engine_PVT_Key_B64=$(echo "$Curr_Engine_Key_Ring"  | jq -r ".\"${Engine_KEY_ID}\".pvt_key")
    Engine_PUBKEY=$($CALL_CONS -c "exportpub $Engine_KEY_ID"|grep -i 'imported key:')
    # Engine_PUBKEY_B64=$(echo "$Engine_PUBKEY"| awk '{print $4}')
    Engine_PUBKEY_HEX=$(echo "$Engine_PUBKEY"| awk '{print $3}')

    echo "Only one keyset in engine!"
    echo "Elections ID: $Elec_0"
    echo "     Engine ADNL: $Engine_ADNL"
    echo "  Engine Pub key: $Engine_PUBKEY_HEX"
else
    Next_Engine_Elec_ID=$((Elec_0 > Elec_1 ? Elec_0 : Elec_1))
    Curr_Engine_Elec_ID=$((Elec_0 < Elec_1 ? Elec_0 : Elec_1))
    Curr_Engine_ADNL=$(echo "$Curr_Engine_Val_Keys" | jq -r ".[]|select(.election_id == $Curr_Engine_Elec_ID)|.validator_adnl_key_id" \
        | base64 -d|od -t xC -An|tr -d '\n'|tr -d ' ')
    Curr_Engine_ADNL_Base64=$(echo "$Curr_Engine_Val_Keys" | jq -r ".[]|select(.election_id == $Curr_Engine_Elec_ID)|.validator_adnl_key_id")
    Next_Engine_ADNL=$(echo "$Curr_Engine_Val_Keys" | jq -r ".[]|select(.election_id == $Next_Engine_Elec_ID)|.validator_adnl_key_id" \
        | base64 -d|od -t xC -An|tr -d '\n'|tr -d ' ')
    Next_Engine_ADNL_Base64=$(echo "$Curr_Engine_Val_Keys" | jq -r ".[]|select(.election_id == $Next_Engine_Elec_ID)|.validator_adnl_key_id")

    Curr_Engine_KEY_ID=$(echo "$Curr_Engine_Val_Keys"      | jq -r ".[]|select(.election_id == $Curr_Engine_Elec_ID)|.validator_key_id")
    Next_Engine_KEY_ID=$(echo "$Curr_Engine_Val_Keys"      | jq -r ".[]|select(.election_id == $Next_Engine_Elec_ID)|.validator_key_id")
    # Curr_Engine_PVT_Key_B64=$(echo "$Curr_Engine_Key_Ring" | jq -r ".\"${Curr_Engine_KEY_ID}\".pvt_key")
    # Next_Engine_PVT_Key_B64=$(echo "$Curr_Engine_Key_Ring" | jq -r ".\"${Next_Engine_KEY_ID}\".pvt_key")

    # Curr_Engine_PUBKEY=`$CALL_CONS -c "exportpub $Curr_Engine_KEY_ID"     | grep -i 'imported key:'`
    Curr_Engine_PUBKEY_B64=$($CALL_CONS -c "exportpub $Curr_Engine_KEY_ID" | grep -i 'imported key:' | awk '{print $4}')
    Curr_Engine_PUBKEY_HEX=$($CALL_CONS -c "exportpub $Curr_Engine_KEY_ID" | grep -i 'imported key:' | awk '{print $3}')

    Next_Engine_PUBKEY=$($CALL_CONS -c "exportpub $Next_Engine_KEY_ID"|grep -i 'imported key:')
    Next_Engine_PUBKEY_B64=$(echo "$Next_Engine_PUBKEY" | awk '{print $4}')
    Next_Engine_PUBKEY_HEX=$(echo "$Next_Engine_PUBKEY" | awk '{print $3}')

    echo "Current Elections ID: $Curr_Engine_Elec_ID"
    echo "     Engine ADNL: $Curr_Engine_ADNL | $Curr_Engine_ADNL_Base64"
    echo "  Engine Pub key: $Curr_Engine_PUBKEY_HEX | $Curr_Engine_PUBKEY_B64"
    echo 
    echo "Next Elections ID: $Next_Engine_Elec_ID"
    echo "     Engine ADNL: $Next_Engine_ADNL | $Next_Engine_ADNL_Base64"
    echo "  Engine Pub key: $Next_Engine_PUBKEY_HEX | $Next_Engine_PUBKEY_B64"
    echo "---------------------------------------------------------------------------------------------"
fi

exit 0
