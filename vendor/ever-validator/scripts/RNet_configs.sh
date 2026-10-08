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
source "${SCRIPT_DIR}/env.sh"

echo "{\"rustnet_configs\":[" > RustNet_Conf_List.json

for ((i=0; i <= 255; i++ ))
do
    CurrParam="$($CALL_CONS -c "getconfig ${i}" |sed -e '1,/GIT_BRANCH/d'|sed 's/config param: //')"
    if [[ "$CurrParam" == "{}" ]];then
        CurrParam="{\"p${i}}\": null}"
        continue
    fi
    echo "${CurrParam}," >> RustNet_Conf_List.json
done

truncate -s -2 RustNet_Conf_List.json
echo -e "\n]}" >> RustNet_Conf_List.json

exit 0
