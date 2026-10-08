#!/usr/bin/env bash
# shellcheck source=env.sh

DINFO_STRT_TIME=$(date +%s)

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

set -o pipefail
####################################
# we can't work on desynced node
export LC_NUMERIC="C"
####################################

echo
echo "#################################### Depool INFO script ########################################"
SelfScriptName=$(basename "$0")
echo "INFO: $SelfScriptName BEGIN $(date +%s) / $(date  +'%F %T %Z')"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if ! source "${SCRIPT_DIR}/env.sh"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't load env.sh"
    exit 1
fi
source "${SCRIPT_DIR}/functions.shinc"

#=================================================
echo
echo -e "$(DispEnvInfo)"
echo
echo -e "$(Determine_Current_Network)"
echo

##############################################################################
# Load addresses and set variables
# net id - first 16 syms of zerostate id

Depool_Name=$1
if [[ -z $Depool_Name ]];then
    Depool_Name="depool"
    Depool_addr="$(cat "${KEYS_DIR}/${Depool_Name}.addr")"
    if [[ -z $Depool_addr ]];then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find depool address file! ${KEYS_DIR}/${Depool_Name}.addr"
        exit 1
    fi
else
    Depool_addr=$Depool_Name
    acc_fmt="$(echo "$Depool_addr" |  awk -F ':' '{print $2}')"
    [[ -z $acc_fmt ]] && Depool_addr="$(cat "${KEYS_DIR}/${Depool_Name}.addr")"
fi
if [[ -z $Depool_addr ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find depool address file! ${KEYS_DIR}/${Depool_Name}.addr"
    exit 1
fi

dpc_addr="$(echo "$Depool_addr" | cut -d ':' -f 2)"
dpc_wc=$(echo "$Depool_addr" | cut -d ':' -f 1)
if [[ ${#dpc_addr} -ne 64 ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Wrong depool address! ${Depool_addr}"
    exit 1
fi
if [[ ${dpc_wc} -lt 0 ]] || [[ ${dpc_wc} -gt 3 ]];then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Wrong workchain for depool address! ${Depool_addr}"
    exit 1
fi

Validator_addr="$(cat "${KEYS_DIR}/${VALIDATOR_NAME}.addr")"
if [[ -z $Validator_addr ]];then
    echo "+++-WARNING(${SelfScriptName} line $LINENO): Can't find local validator address file! ${KEYS_DIR}/${VALIDATOR_NAME}.addr"
fi
echo "INFO: Local validator account address: $Validator_addr"

#=================================================
# If the access mode is set to console and the node is not synchronized, prompt the user to switch to dapp mode temporarily
if Ask_to_switch_to_dapp;then
    echo "Proceeding..."
    export FORCE_USE_DAPP=true
elif [[ $? -gt 1 ]];then
    echo "Cancelled."
    exit 1
fi

##############################################################################
# Get Elections Time parameters
CONFIG_PAR_15="$(Get_NetConfig_P15)"
validators_elected_for=$(echo "$CONFIG_PAR_15" | awk '{print $1}')
##############################################################################
# get elections ID from elector
echo
echo "==================== Elections Info ====================================="
declare -i elections_id
elections_id=$(Get_Current_Elections_ID)
if [[ $elections_id -eq 0 ]];then
    echo -e "   ${BoldText}=> There are no Elections now.${NormText}"
else
    echo "   => Elector Elections ID: $elections_id / $(echo "$elections_id" | gawk '{print strftime("%Y-%m-%d %H:%M:%S", $1)}')"
fi
echo

Node_Keys="$(Get_Engine_ADNL)"
if [[ $Node_Keys  != "null" ]];then
    Curr_Engine_Eclec_ID=$(echo "$Node_Keys" | awk '{print $2}')
    Curr_Engine_ADNL_Key=$(echo "$Node_Keys" | awk '{print $1}'|tr "[:upper:]" "[:lower:]")
    Next_Engine_Eclec_ID=$(echo "$Node_Keys" | awk '{print $4}')
    Next_Engine_ADNL_Key=$(echo "$Node_Keys" | awk '{print $3}'|tr "[:upper:]" "[:lower:]")

    if [[ -z $Next_Engine_Eclec_ID ]];then
        echo "Current Engine Elections ID: $Curr_Engine_Eclec_ID / $(echo "$Curr_Engine_Eclec_ID" | gawk '{print strftime("%Y-%m-%d %H:%M:%S", $1)}')"
        echo "    Current Engine ADNL key: $Curr_Engine_ADNL_Key"
    else
        echo "Current Engine Elections ID: $Curr_Engine_Eclec_ID / $(echo "$Curr_Engine_Eclec_ID" | gawk '{print strftime("%Y-%m-%d %H:%M:%S", $1)}')"
        echo "    Current Engine ADNL key: $Curr_Engine_ADNL_Key"
        echo
        echo "   Next Engine Elections ID: $Next_Engine_Eclec_ID / $(echo "$Next_Engine_Eclec_ID" | gawk '{print strftime("%Y-%m-%d %H:%M:%S", $1)}')"
        echo "       Next Engine ADNL key: $Next_Engine_ADNL_Key"
    fi
else
    echo "+++-WARNING(${SelfScriptName} line $LINENO): There is no information about elections keys in the engine. You may not have participated in any elections yet."
fi

#######################################################################################
# Get Depool Info
# returns (
#    {"name":"poolClosed","type":"bool"},
#    {"name":"minStake","type":"uint64"},
#    {"name":"validatorAssurance","type":"uint64"},
#    {"name":"participantRewardFraction","type":"uint8"},
#    {"name":"validatorRewardFraction","type":"uint8"},
#    {"name":"balanceThreshold","type":"uint64"},
#    {"name":"validatorWallet","type":"address"},
#    {"name":"proxies","type":"address[]"},
#    {"name":"stakeFee","type":"uint64"},
#    {"name":"retOrReinvFee","type":"uint64"},
#    {"name":"proxyFee","type":"uint64"}

echo 
echo "==================== Current Depool State ====================================="
#============================================
# check depool contract status
Depool_Info="$(Get_Account_Info "$Depool_addr")"
Depool_Acc_State="$(echo "$Depool_Info" |awk '{print $1}')"
if [[ "$Depool_Acc_State" == "None" ]];then
    echo -e "${BoldText}${RedBack}###-ERROR(${SelfScriptName} line $LINENO): Depool Account does not exist! (no tokens, no code, nothing)${NormText}"
    echo
    exit 0
elif [[ "$Depool_Acc_State" == "Uninit" ]];then
    echo -e "${BoldText}${RedBack}###-ERROR(${SelfScriptName} line $LINENO): Depool Account does not deployed.${NormText}"
    echo "Has balance : $(echo "$Depool_Info" |awk '{print $2}') nanotokens ()"
    echo
    exit 0
fi

#============================================
# get info from DePool contract state
Current_Depool_Info="$(Get_DP_Info "$Depool_addr")"
if ! Get_SC_current_state "$Depool_addr";then
    echo -e "${BoldText}${RedBack}###-ERROR(${SelfScriptName} line $LINENO): Depool Account state does not received.${NormText}"
    echo
fi

PoolClosed=$(jq -r '.poolClosed' <<< "$Current_Depool_Info")
if [[ "$PoolClosed" == "false" ]];then
    PoolState="${GreenBack}${BoldText}OPEN for participation!${NormText}"
elif [[ "$PoolClosed" == "true" ]];then
    PoolState="${RedBlink}${BoldText}CLOSED!!! all stakes should be return to participants${NormText}"
else
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't determine the Depool state!! All following data is invalid!!!"
fi
echo -e "Pool State: $PoolState"
echo
echo "==================== Depool addresses ====================================="

DP_Owner_Addr=$(jq -r ".validatorWallet" <<< "$Current_Depool_Info")
dp_proxy0=$(jq -r ".proxies[0]" <<< "$Current_Depool_Info")
dp_proxy1=$(jq -r ".proxies[1]" <<< "$Current_Depool_Info")
[[ ! -f "${KEYS_DIR}/proxy0.addr" ]] && echo "$dp_proxy0" > "${KEYS_DIR}/proxy0.addr"
[[ ! -f "${KEYS_DIR}/proxy1.addr" ]] && echo "$dp_proxy1" > "${KEYS_DIR}/proxy1.addr"

#============================================  
# Get balances
Depool_Bal=$(Get_Account_Info "$Depool_addr" | awk '{print $2}')
Depool_Self_Balance=$($CALL_CLI -j run --boc "${INPL_ELECTIONS_WORK_DIR}/${Depool_addr##*:}.boc" --abi "$INPL_DePool_ABI" getDePoolBalance '{}' | jq -r '.value0')
Val_Bal=$(Get_Account_Info "$DP_Owner_Addr"| awk '{print $2}')
prx0_Bal=$(Get_Account_Info "$dp_proxy0"| awk '{print $2}')
prx1_Bal=$(Get_Account_Info "$dp_proxy1"| awk '{print $2}')

#============================================
# Get depool fininfo
PoolSelfMinBalance=$(jq -r '.balanceThreshold' <<< "$Current_Depool_Info")
PoolMinStake=$(jq -r '.minStake' <<< "$Current_Depool_Info")
validatorAssurance=$(jq -r '.validatorAssurance' <<< "$Current_Depool_Info")
ValRewardFraction=$(jq -r '.validatorRewardFraction' <<< "$Current_Depool_Info")
PoolValStakeFee=$(jq -r '.stakeFee' <<< "$Current_Depool_Info")
PoolRetOrReinvFee=$(jq -r '.retOrReinvFee' <<< "$Current_Depool_Info")

#============================================
# Get depool rounds info
Depool_Rounds_Info="$(Get_DP_Rounds "$Depool_addr")"
Curr_Rounds_Info="$(Rounds_Sorting_by_ID "$Depool_Rounds_Info")"

# ------------------------------------------------------------------------------------------------------------------------
Prev_DP_Elec_ID=$(jq -r ".[0].supposedElectedAt" <<< "$Curr_Rounds_Info" | xargs printf "%10d\n")
Prev_DP_Round_ID=$(jq -r ".[0].id"             <<< "$Curr_Rounds_Info" | xargs printf "%d\n")
Prev_Round_P_QTY=$(jq -r ".[0].participantQty" <<< "$Curr_Rounds_Info" | xargs printf "%4d\n")
Prev_Round_Stake=$(jq -r ".[0].stake"          <<< "$Curr_Rounds_Info" | xargs printf "%d\n")
# shellcheck disable=SC2086
Prev_Round_Stake=$(printf '%12.3f' "$(echo "scale=3; ${Prev_Round_Stake}" / 1000000000 | $CALL_BC)")
# Prev_Round_Reward=$(printf '%12.3f' "$(echo "scale=3; ${Prev_Round_Reward}" / 1000000000 | $CALL_BC)")

Curr_DP_Elec_ID=$( jq -r ".[1].supposedElectedAt" <<< "$Curr_Rounds_Info" | xargs printf "%10d\n")
Curr_Round_P_QTY=$(jq -r ".[1].participantQty"    <<< "$Curr_Rounds_Info" | xargs printf "%4d\n")
Curr_DP_Round_ID=$(jq -r ".[1].id"                <<< "$Curr_Rounds_Info" | xargs printf "%d\n")
Curr_Round_Stake_nT=$(jq -r ".[1].stake"          <<< "$Curr_Rounds_Info" | xargs printf "%d\n")
# shellcheck disable=SC2086
Curr_Round_Stake=$(printf '%12.3f' "$(echo "scale=3; ${Curr_Round_Stake_nT}" / 1000000000 | $CALL_BC)")

Next_DP_Elec_ID=$(jq -r ".[2].supposedElectedAt" <<< "$Curr_Rounds_Info"| xargs printf "%d\n")
[[ $Next_DP_Elec_ID -eq 0 ]] && Next_DP_Elec_ID=$((Curr_DP_Elec_ID + validators_elected_for))
Next_DP_Round_ID=$(jq -r ".[2].id"             <<< "$Curr_Rounds_Info"  | xargs printf "%d\n")
Next_Round_P_QTY=$(jq -r ".[2].participantQty" <<< "$Curr_Rounds_Info"  | xargs printf "%4d\n")
Next_Round_Stake_nT=$(jq -r ".[2].stake"       <<< "$Curr_Rounds_Info"  | xargs printf "%d\n")
# shellcheck disable=SC2086
Next_Round_Stake=$(printf '%12.3f' "$(echo "scale=3; ${Next_Round_Stake_nT}" / 1000000000  | $CALL_BC)")

echo "Depool contract address:     $Depool_addr  Balance: $(echo "scale=3; $((Depool_Bal)) / 1000000000" | $CALL_BC)"
echo "Depool self balance:         $(echo "scale=3; $((Depool_Self_Balance)) / 1000000000" | $CALL_BC)"
echo -e "${BoldText}REMEMBER: Depool CRITICAL_THRESHOLD is 10 tokens. If the depool balance is less than 10 tokens, the depool will be stuck and will not be able to operate at all.${NormText}"
echo "Depool Owner/validator addr: $DP_Owner_Addr  Balance: $(echo "scale=3; $((Val_Bal)) / 1000000000" | $CALL_BC)"
echo "Depool proxy #0:            $dp_proxy0  Balance: $(echo "scale=3; $((prx0_Bal)) / 1000000000" | $CALL_BC)"
echo "Depool proxy #1:            $dp_proxy1  Balance: $(echo "scale=3; $((prx1_Bal)) / 1000000000" | $CALL_BC)"
echo
echo "================ Finance information for the depool ==========================="

echo "                Pool Min Stake (Tk): $(echo "scale=3; $((PoolMinStake)) / 1000000000" | $CALL_BC)"
echo "            Validator Comission (%): $((ValRewardFraction))"
echo "              Depool stake fee (Tk): $(echo "scale=3; $((PoolValStakeFee)) / 1000000000" | $CALL_BC)"
echo " Depool return or reinvest fee (Tk): $(echo "scale=3; $((PoolRetOrReinvFee)) / 1000000000" | $CALL_BC)"
echo " Depool min balance to operate (Tk): $(echo "scale=3; $((PoolSelfMinBalance)) / 1000000000" | $CALL_BC)"
echo "           Validator Assurance (Tk): $((validatorAssurance / 1000000000))"
echo
##################################################################################################################
echo "============================ Depool rounds info ==============================="
echo " --------------------------------------------------------------------------------------------------------------------------"
echo "|                 |              Prev Round          |           Current Round          |              Next Round          |"
echo " --------------------------------------------------------------------------------------------------------------------------"
echo "|        Seq No   |       $(printf '%12d' "$Prev_DP_Round_ID")               |       $(printf '%12d' "$Curr_DP_Round_ID")               |       $(printf '%12d' "$Next_DP_Round_ID")               |"
echo "|            ID   | $Prev_DP_Elec_ID / $(echo "$Prev_DP_Elec_ID" | gawk '{print strftime("%Y-%m-%d %H:%M:%S", $1)}') | $Curr_DP_Elec_ID / $(echo "$Curr_DP_Elec_ID" | gawk '{print strftime("%Y-%m-%d %H:%M:%S", $1)}') | $Next_DP_Elec_ID / $(echo "$Next_DP_Elec_ID" | gawk '{print strftime("%Y-%m-%d %H:%M:%S", $1)}') |"
echo "| Participant QTY |               $Prev_Round_P_QTY               |               $Curr_Round_P_QTY               |               $Next_Round_P_QTY               |"
echo "|         Stake   |           $Prev_Round_Stake           |           $Curr_Round_Stake           |           $Next_Round_Stake           |"
# echo "|        Reward   |           $Prev_Round_Reward           |           $Curr_Round_Reward           |           $Next_Round_Reward           |"
echo " --------------------------------------------------------------------------------------------------------------------------"
echo
##################################################################################################################
echo "==================== Depool Owner Ordinary, Lock & Vesting stakes info ========================"
if ! DP_Owner_Info="$(Get_DP_Part_Info "$Depool_addr" "$DP_Owner_Addr")";then
    DINFO_END_TIME=$(date +%s)
    Dinfo_mins=$(( (DINFO_END_TIME - DINFO_STRT_TIME)/60 ))
    Dinfo_secs=$(( (DINFO_END_TIME - DINFO_STRT_TIME)%60 ))
    echo -e "\n###-ERROR(line $LINENO): Can't get depool owner stakes info! It seems that the depool owner has not any stakes.\n"
    echo "Depool requires owner stakes to be not less than the validator assurance in each round."
    echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
    echo "Gather info took $Dinfo_mins min $Dinfo_secs secs"
    echo "================================================================================================"
    exit 1
fi

Lock_Stake_Donor="$(jq -r '.lockDonor' <<< "$DP_Owner_Info")"
Lock_Stake_Round_0_Info="$(jq '[.locks[]]|.[0]' <<< "$DP_Owner_Info")"
if [[ "${Lock_Stake_Round_0_Info}" != "null" ]];then
    Lock_Stake_Round_1_Info="$(jq '[.locks[]]|.[1]' <<< "$DP_Owner_Info")"
    Lock_Stake_Round_0_Amount_nT="$(jq -r '.remainingAmount' <<< "$Lock_Stake_Round_0_Info")"
    Lock_Stake_Round_1_Amount_nT="$(jq -r '.remainingAmount' <<< "$Lock_Stake_Round_1_Info")"

    Lock_Stake_Round_0_Amount="$(printf '%12.3f' "$(echo "scale=3; $Lock_Stake_Round_0_Amount_nT" / 1000000000 | $CALL_BC)")"
    # Lock_Stake_Round_0_Amount="$(printf '%12.3f' "$(echo "scale=3; $Lock_Stake_Round_0_Amount_nT / 1000000000" | $CALL_BC)")"

    Lock_Stake_Round_1_Amount="$(printf '%12.3f' "$(echo "scale=3; $Lock_Stake_Round_1_Amount_nT" / 1000000000 | $CALL_BC)")"
    # Lock_Stake_Round_1_Amount="$(printf '%12.3f' "$(echo "scale=3; $Lock_Stake_Round_1_Amount_nT / 1000000000" | $CALL_BC)")"
    
    Lock_Stake_Setted="$( jq -r '.lastWithdrawalTime' <<< "$Lock_Stake_Round_0_Info")"
    Lock_Stake_Set_For="$(jq -r '.withdrawalPeriod' <<< "$Lock_Stake_Round_0_Info")"
    Lock_Stake_Out_Date=$((Lock_Stake_Setted + Lock_Stake_Set_For))
fi
# =================================================================================================================
Vest_Stake_Donor="$(jq -r '.vestingDonor' <<< "$DP_Owner_Info")"
Vest_Stake_Round_0_Info="$(jq '[.vestings[]]|.[0]' <<< "$DP_Owner_Info")"
if [[ "${Vest_Stake_Round_0_Info}" != "null" ]];then
    Vest_Stake_Round_1_Info="$(jq '[.vestings[]]|.[1]' <<< "$DP_Owner_Info")"
    Vest_Stake_Round_0_Amount_nT="$(jq -r '.remainingAmount' <<< "${Vest_Stake_Round_0_Info}")"
    Vest_Stake_Round_1_Amount_nT="$(jq -r '.remainingAmount' <<< "${Vest_Stake_Round_1_Info}")"
    Vest_Stake_Round_0_Amount="$(printf '%12.3f' "$(echo "scale=3; ${Vest_Stake_Round_0_Amount_nT}" / 1000000000 | $CALL_BC)")"
    Vest_Stake_Round_1_Amount="$(printf '%12.3f' "$(echo "scale=3; ${Vest_Stake_Round_1_Amount_nT}" / 1000000000 | $CALL_BC)")"
    WSWD="$(jq -r '.lastWithdrawalTime' <<< "${Vest_Stake_Round_0_Info}")"
    WSWP="$(jq -r '.withdrawalPeriod' <<< "${Vest_Stake_Round_0_Info}")"
    VS_Withdr_Date_0=$((WSWD + WSWP))
    WSWD="$(jq -r '.lastWithdrawalTime' <<< "${Vest_Stake_Round_1_Info}")"
    WSWP="$(jq -r '.withdrawalPeriod' <<< "${Vest_Stake_Round_1_Info}")"
    # VS_Withdr_Date_1=$((WSWD + WSWP) "$Vest_Stake_Round_0_Info"
    VS_Withdr_Amount_0_nt="$(jq -r '.withdrawalValue' <<< "$Vest_Stake_Round_0_Info")"
    VS_Withdr_Amount_1_nt="$(jq -r '.withdrawalValue' <<< "$Vest_Stake_Round_1_Info")"
    # VS_Withdr_Amount=$((VS_Withdr_Amount_0_nt + VS_Withdr_Amount_1_nt))
    VS_Withdr_Amount_0="$(printf '%12.3f' "$(echo "scale=3; ${VS_Withdr_Amount_0_nt}" / 1000000000 | $CALL_BC)")"
    VS_Withdr_Amount_1="$(printf '%12.3f' "$(echo "scale=3; ${VS_Withdr_Amount_1_nt}" / 1000000000 | $CALL_BC)")"
fi

echo " --------------------------------------------------------------------------------------------------------------------------"
echo "|                 |              Prev Round          |           Current Round          |      Lock stake return day       |"
echo " --------------------------------------------------------------------------------------------------------------------------"
echo "|                                               LOCK STAKE                                                                 |"
echo "| Donor:  $Lock_Stake_Donor                                               |"
if [[ "${Lock_Stake_Round_0_Info}" != "null" ]];then
echo "| Remain Amount   |          $Lock_Stake_Round_0_Amount            |            $Lock_Stake_Round_1_Amount          |       $(echo "$Lock_Stake_Out_Date" | gawk '{print strftime("%Y-%m-%d %H:%M:%S", $1)}')        |"
else
echo "|                                         YOU HAVE NO LOCK STAKE                                                           |"
fi
echo " --------------------------------------------------------------------------------------------------------------------------"
echo "|                                             VESTING STAKE                             |"
echo "| Donor:  $Vest_Stake_Donor            |"
if [[ "${Vest_Stake_Round_0_Info}" != "null" ]];then
echo "| Remain Amount   |          $Vest_Stake_Round_0_Amount            |            $Vest_Stake_Round_1_Amount          |"
echo "| Withdrow Date   |        $(echo "$VS_Withdr_Date_0"|gawk '{print strftime("%Y-%m-%d %H:%M:%S", $1)}')       |          $(echo "$VS_Withdr_Date_0" | gawk '{print strftime("%Y-%m-%d %H:%M:%S", $1)}')     |"
echo "| Withdrow Amount |          $VS_Withdr_Amount_0            |            $VS_Withdr_Amount_1          |"
else
echo "|                                     YOU HAVE NO VESTING STAKE                         |"
fi
echo " ---------------------------------------------------------------------------------------"

##################################################################################################################
echo
echo "=================== Current participants info in the depool ==================="

Participants_List="$(Get_DP_Parts_List "$Depool_addr")"

Num_of_participants=$(jq '.participants|length' <<< "$Participants_List")
echo "Current Number of participants: $Num_of_participants"
echo

Prev_Round_Part_QTY=$(jq -r ".[0].participantQty" <<< "$Curr_Rounds_Info" | xargs printf "%d\n")
Curr_Round_Part_QTY=$(jq -r ".[1].participantQty" <<< "$Curr_Rounds_Info" | xargs printf "%d\n")
Next_Round_Part_QTY=$(jq -r ".[2].participantQty" <<< "$Curr_Rounds_Info" | xargs printf "%d\n")

##################################################################################################################
echo "===== Current Round participants QTY (prev/curr/next/lock): $((Prev_Round_Part_QTY + 1)) / $((Curr_Round_Part_QTY + 1)) / $((Next_Round_Part_QTY + 1))"

CRP_QTY=$((Curr_Round_Part_QTY - 1))
for (( i=0; i <= $CRP_QTY; i++ ))
do
    Curr_Part_Addr="$(jq -r ".participants|.[$i]" <<< "$Participants_List")"
    Current_Participant_Info="$(Get_DP_Part_Info "$Depool_addr" "$Curr_Part_Addr")"

    Prev_Ord_Stake=$(jq -r ".stakes.\"$Prev_DP_Round_ID\"" <<< "$Current_Participant_Info")
    POS_Info=$(printf "%'9.2f" "$(echo "scale=3; $((Prev_Ord_Stake)) / 1000000000" | $CALL_BC)")
    
    Curr_Ord_Stake=$(jq -r ".stakes.\"$Curr_DP_Round_ID\"" <<< "$Current_Participant_Info")
    COS_Info=$(printf "%'9.2f" "$(echo "scale=3; $((Curr_Ord_Stake)) / 1000000000" | $CALL_BC)")
    
    Next_Ord_Stake=$(jq -r ".stakes.\"$Next_DP_Round_ID\"" <<< "$Current_Participant_Info")
    NOS_Info=$(printf "%'9.2f" "$(echo "scale=3; $((Next_Ord_Stake)) / 1000000000" | $CALL_BC)")
    
    Reward=$(jq -r ".reward" <<< "$Current_Participant_Info")
    RWRD_Info=$(printf "%'8.2f" "$(echo "scale=3; $((Reward)) / 1000000000" | $CALL_BC)")

    Reinvest=$(jq -r ".reinvest" <<< "$Current_Participant_Info")
    REINV_Info=""
    if [[ "${Reinvest}" == "false" ]];then
        REINV_Info="${RedBack}GONE${NormText}"
    elif [[ "${Reinvest}" == "true" ]];then
        REINV_Info="Stay"
    fi

    Wtdr_Val_hex=$(jq -r ".withdrawValue" <<< "$Current_Participant_Info")
    Wtdr_Val_Info=""
    if [[ $Wtdr_Val_hex -ne 0 ]];then
        Wtdr_Val_Info="; Next round withdraw: $(echo "scale=3; $((Wtdr_Val_hex)) / 1000000000" | $CALL_BC)"
    fi

    #--------------------------------------------
    echo -e "$(printf '%4d' $(($i + 1))) $Curr_Part_Addr Reward: $RWRD_Info ; Stakes(${REINV_Info}): $POS_Info / $COS_Info / $NOS_Info   $Wtdr_Val_Info"
    #--------------------------------------------
done

##################################################################################################################
echo
echo "===== Total Depool participants (prev/curr/next/lock) =============================="

CRP_QTY=$((Num_of_participants - 1))
for (( i=0; i <= $CRP_QTY; i++ ))
do
    Curr_Part_Addr="$(jq -r ".participants|.[$i]" <<< "$Participants_List")"
    Current_Participant_Info="$(Get_DP_Part_Info "$Depool_addr" "$Curr_Part_Addr")"

    Prev_Ord_Stake=$(jq -r ".stakes.\"$Prev_DP_Round_ID\"" <<< "$Current_Participant_Info")
    POS_Info=$(printf "%'8.2f" "$(echo "scale=3; $((Prev_Ord_Stake)) / 1000000000" | $CALL_BC)")
    
    Curr_Ord_Stake=$(jq -r ".stakes.\"$Curr_DP_Round_ID\"" <<< "$Current_Participant_Info")
    COS_Info=$(printf "%'8.2f" "$(echo "scale=3; $((Curr_Ord_Stake)) / 1000000000" | $CALL_BC)")
    
    Next_Ord_Stake=$(jq -r ".stakes.\"$Next_DP_Round_ID\"" <<< "$Current_Participant_Info")
    NOS_Info=$(printf "%'8.2f" "$(echo "scale=3; $((Next_Ord_Stake)) / 1000000000" | $CALL_BC)")

    Vesting_Stake=$(jq -r '[.vestings[]][0].remainingAmount' <<< "$Current_Participant_Info")
    VOS_Info=$(printf "%'8.2f" "$(echo "scale=3; $((Vesting_Stake *2)) / 1000000000" | $CALL_BC)")

    Reward=$(jq -r ".reward" <<< "$Current_Participant_Info")
    RWRD_Info=$(printf "%'8.2f" "$(echo "scale=3; $((Reward)) / 1000000000" | $CALL_BC)")

    Reinvest=$(jq -r ".reinvest" <<< "$Current_Participant_Info")
    REINV_Info=""
    if [[ "${Reinvest}" == "false" ]];then
        REINV_Info="${RedBack}GONE${NormText}"
    elif [[ "${Reinvest}" == "true" ]];then
        REINV_Info="Stay"
    fi

    Wtdr_Val_hex=$(jq -r ".withdrawValue" <<< "$Current_Participant_Info")
    Wtdr_Val_Info=""
    if [[ $Wtdr_Val_hex -ne 0 ]];then
        Wtdr_Val_Info="; Next round withdraw: $(echo "scale=3; $((Wtdr_Val_hex)) / 1000000000" | $CALL_BC)"
    fi

    #--------------------------------------------
    echo -e "$(printf '%4d' $(($i + 1))) $Curr_Part_Addr Reward: $RWRD_Info ; Stakes(${REINV_Info}): $POS_Info / $COS_Info / $NOS_Info   $Wtdr_Val_Info Vesting: $VOS_Info"
    #--------------------------------------------
done

DINFO_END_TIME=$(date +%s)
Dinfo_mins=$(( (DINFO_END_TIME - DINFO_STRT_TIME)/60 ))
Dinfo_secs=$(( (DINFO_END_TIME - DINFO_STRT_TIME)%60 ))
echo
echo "+++INFO: $SelfScriptName FINISHED $(date +%s) / $(date  +'%F %T %Z')"
echo "Gather info took $Dinfo_mins min $Dinfo_secs secs"
echo "================================================================================================"

exit 0

