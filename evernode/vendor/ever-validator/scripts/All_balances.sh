#!/usr/bin/env bash

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
if ! source "${SCRIPT_DIR}/env.sh"; then
    echo "###-ERROR: Can't load env.sh"
    exit 1
fi
source "${SCRIPT_DIR}/functions.shinc"

export LC_NUMERIC="C"

Addr_List="${KEYS_DIR}/Addr_list.json"
if [[ "$1" == "all" ]];then
    Addr_List="${KEYS_DIR}/All_nodes_Addr_list.json"
fi
if [[ ! -f "$Addr_List" ]];then
    echo "Error: No file with address list `$Addr_List` "
    exit 1
fi

Addr_QTY=$(cat $Addr_List | jq '[.Addresses[]]|length')

echo
echo "Now is $(date +'%F %T %Z')"
declare -i MSIG_Total_Balance=0
declare -i TIK_Total_Balance=0
declare -i DP_Total_Balance=0
echo "       Name      MSIG         Tik       DePool"
for (( i=0; i<$Addr_QTY; i++ ))
do
    Name=$(cat "$Addr_List"|jq -r "[.Addresses[]]|.[${i}]|keys[]")

    MSIG_Addr=$(cat "$Addr_List"|jq -r "[.Addresses[]]|.[${i}].${Name}.msig")
    MSIG_Balance_nT=$($CALL_CLI account "$MSIG_Addr" |grep -i 'balance'|awk '{print $2}')
    MSIG_Total_Balance=$((MSIG_Total_Balance + MSIG_Balance_nT))
    MSIG_Balance=$(printf "%'9.2f" "$(echo $((MSIG_Balance_nT)) / 1000000000 | jq -nf /dev/stdin)")

    TIK_Balance=""
    TIK_Addr=$(cat "$Addr_List"|jq -r "[.Addresses[]]|.[${i}].${Name}.Tik")
    if [[ -n $TIK_Addr ]];then
        TIK_Balance_nT=$($CALL_CLI account "$TIK_Addr" |grep -i 'balance'|awk '{print $2}')
        TIK_Total_Balance=$((TIK_Total_Balance + TIK_Balance_nT))
        TIK_Balance=$(printf "%'9.2f" "$(echo $((TIK_Balance_nT)) / 1000000000 | jq -nf /dev/stdin)")
    fi
    
    DP_Balance=""
    DP_Addr=$(cat "$Addr_List"|jq -r "[.Addresses[]]|.[${i}].${Name}.depool")
    if [[ -n $DP_Addr ]];then
        DP_Balance_nT=`$CALL_CLI account "$DP_Addr" |grep -i 'balance'|awk '{print $2}'`
        DP_Total_Balance=$((DP_Total_Balance + DP_Balance_nT))
        DP_Balance=$(printf "%'9.2f" "$(echo $((DP_Balance_nT)) / 1000000000 | jq -nf /dev/stdin)")
    fi

    echo "$(printf "%'10s" "$Name"): $MSIG_Balance   $TIK_Balance    $DP_Balance"
done
echo "---------------------------------------------------------------------------"
echo "     TOTAL: $(printf "%'9.2f" "$(echo $((MSIG_Total_Balance)) / 1000000000 | jq -nf /dev/stdin)")"
echo "========================================================="
exit 0
