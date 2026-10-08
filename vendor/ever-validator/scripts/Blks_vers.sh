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

SCRIPT_DIR=`cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P`
if ! source "${SCRIPT_DIR}/env.sh"; then
    echo "###-ERROR: Can't load env.sh"
    exit 1
fi

BlkVerList="47 46 45 44 43 42 41"
Time_Window=${1:-3600}

# Net="main"
# DApp_URL="https://${Net}.ton.dev"

FORMAT="%'4.1f"
#     2022-02-01 15:12:08 MSK Blocks in main :  0.0 / 0.0  0.0 / 0.0  0.0 / 0.0  0.0 / 0.0  0.0 / 0.0  0.0 / 0.0  34.1 /15.0  55.7 /74.9   7.0 / 6.9   1.6 / 1.0   0.0 / 0.0   1.1 / 2.2   0.0 / 0.4   1.1 / 0.1 
#echo "                          Bloks versions:     26          25          24          23          22          21          20          19 "
#echo "                          Bloks chain   :   MC / WC     MC / WC     MC / WC     MC / WC     MC / WC     MC / WC     MC / WC     MC / WC "

Header1='                         Bloks versions:     '
Header2='                         Bloks chain   :   '

for BlkVer in $BlkVerList;do
    Header1=${Header1}${BlkVer}'          '
    Header2=${Header2}'MC / WC     '
done
echo "${Header1}"
echo "${Header2}"

while true
do
    for Net in mainnet devnet; do
        DApp_URL="https://${Net}.evercloud.dev/$DAPP_Project_id"
        curr_time=$(date +%s)
        Time_Interval=$((curr_time - Time_Window))
        # && echo ${Time_Interval}
        Total_blks_M=$(curl -sS -X POST -g -H "Content-Type: application/json" "${DApp_URL}/graphql" -d "{\"query\": \"query {aggregateBlocks(filter: {gen_utime: {gt: ${Time_Interval}}, workchain_id: {eq: -1} }) }\"}"|jq -r '.data.aggregateBlocks[0]')
        Total_blks_W=$(curl -sS -X POST -g -H "Content-Type: application/json" "${DApp_URL}/graphql" -d "{\"query\": \"query {aggregateBlocks(filter: {gen_utime: {gt: ${Time_Interval}}, workchain_id: {eq:  0} }) }\"}"|jq -r '.data.aggregateBlocks[0]')
        echo -n "$(date  +'%F %T %Z')"
        echo -n " Blocks in $(printf "%'4s" $Net) :"

        for BlkVer in $BlkVerList; do 
            Ver_M=$(curl -sS -X POST -g -H "Content-Type: application/json" "${DApp_URL}/graphql" -d "{\"query\": \"query {aggregateBlocks(filter: {gen_utime: {gt: ${Time_Interval}}, gen_software_version: {eq: $BlkVer}, workchain_id: {eq: -1} }) }\"}"|jq -r '.data.aggregateBlocks[0]')
            Ver_W=$(curl -sS -X POST -g -H "Content-Type: application/json" "${DApp_URL}/graphql" -d "{\"query\": \"query {aggregateBlocks(filter: {gen_utime: {gt: ${Time_Interval}}, gen_software_version: {eq: $BlkVer}, workchain_id: {eq:  0} }) }\"}"|jq -r '.data.aggregateBlocks[0]')
            echo -n " $(printf $FORMAT "$(echo "$Ver_M * 100 / $Total_blks_M" | jq -nf /dev/stdin)") /$(printf $FORMAT "$(echo "$Ver_W * 100 / $Total_blks_W" | jq -nf /dev/stdin)") "
        done
        echo
    done
    echo
    sleep 60
done

exit 0
