#!/usr/bin/env bash
# shellcheck shell=bash
# shellcheck disable=SC2031
set -eE

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
echo "################################## Stake to depool script ######################################"
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

# There are 3 types of stakes: ordinary, vesting, lock
# Ordinary stake should be set separately by hand for each round
# Depool doesn't divide stake amount into rounds; it stakes the entire amount to the nearest round
#
# Before making vesting or lock stake, you should set the donor address for each type of stake for each participant
# If you don't set a donor address for lock or vesting stake, you will not be able to make such a stake
# If you don't plan to make lock or vesting stake, you can skip this step
#
# Vesting stake should be set once and will be automatically distributed to all rounds
# Lock stake should be set once and will be automatically distributed to all rounds
#
# To receive a lock or vesting stake, the beneficiary must:
#     already have an ordinary stake of any amount in the DePool
#     set the donor address for each type of stake

function Usage(){
    echo
    echo "Usage: $SelfScriptName <stake_operation> <DEPOOL> <MSIG> <AMOUNT|lock-donor-address|vesting-donor-address> [TOTAL_DAYS] [WITHDRAWAL_DAYS] [BENEFICIARY]"
    echo ""
    echo "  <stake_operation> can be one of the following:"
    echo "    ordinary        - Make an ordinary stake"
    echo "    vesting         - Make a vesting stake"
    echo "    lock            - Make a lock stake"
    echo "    WD-all          - Completely withdraw your ordinary stake from all rounds"
    echo "    WD-part         - Withdraw part of your ordinary stake from the next round"
    echo "    cancelWithdrawal- Cancel withdrawal stake from the next round"
    echo "    remove-stake    - Remove an ordinary stake from a pooling round (if it has not been staked in the Elector yet)"
    echo "    lock-donor      - Set approved donor for lock stake"
    echo "    vesting-donor   - Set approved donor for vesting stake"
    echo "    replenish       - Replenish depool's self balance"
    echo ""
    echo "  <DEPOOL>           - Depool address or name in the keys directory. If '--', the local depool will be used."
    echo "  <MSIG>             - Multisig wallet name in the keys directory. If '--', the \${VALIDATOR_NAME} in keys directory will be used."
    echo "  <AMOUNT>           - Amount of stake in tokens."
    echo "  <lock-donor-address>- Donor name (filename NAME.addr with address in the keys directory) or address for lock stake."
    echo "  <vesting-donor-address>- Donor name (filename NAME.addr with address in the keys directory) or address for lock stake."
    echo ""
    echo "  For vesting and lock stakes, you must additionally specify total and withdrawal periods in days and a beneficiary address:"
    echo ""
    echo "    Usage for Vesting Stake:"
    echo "      $SelfScriptName vesting <DEPOOL> <MSIG> <AMOUNT> <TOTAL_DAYS> <WITHDRAWAL_DAYS> <BENEFICIARY>"
    echo ""
    echo "      <TOTAL_DAYS>       - Total stake period in days (1 to 6570 days). Must be exactly divisible by <WITHDRAWAL_DAYS>."
    echo "      <WITHDRAWAL_DAYS>  - Withdrawal period in days (each time a withdrawal period ends, a portion of the stake is released to the BENEFICIARY)."
    echo "      <BENEFICIARY>      - Beneficiary address in the form x:xx.. or name in the keys directory. The beneficiary must have an existing stake in the depool."
    echo ""
    echo "      Limitations for period settings:"
    echo "        - WITHDRAWAL_DAYS should be <= TOTAL_DAYS."
    echo "        - TOTAL_DAYS cannot exceed 6570 days (18 years) or be <= 0."
    echo "        - TOTAL_DAYS should be exactly divisible by WITHDRAWAL_DAYS."
    echo ""
    echo "  Examples:"
    echo "    $SelfScriptName ordinary depool1 msig1 1000"
    echo "    $SelfScriptName vesting depool1 msig1 1000 3650 365 beneficiary1"
    echo ""
    echo "  Notes:"
    echo "    - Before making a vesting or lock stake, ensure that the donor address is set for each participant."
    echo "    - The beneficiary must already have an ordinary stake in the depool."
    echo "    - Ensure that the depool is open for participation before staking."
    exit 0
}

#===========================================================
# Mandatory parameters for all operations
if [[ $# -lt 4 ]]; then
    echo "###-ERROR: Insufficient parameters provided."
    Usage
fi

STAKE_OP="$1"
DEPOOL_NAME="$2"
MSIG_NAME="$3"
shift 3

#===========================================================
# Check msig and depool names
if [[ "$MSIG_NAME" == "--" ]]; then
    MSIG_NAME="${VALIDATOR_NAME}"
fi
if [[ "$DEPOOL_NAME" == "--" ]]; then
    DEPOOL_NAME="depool"
fi

# Get and check depool address
if ! Depool_addr="$(NameToAddr "$DEPOOL_NAME")"; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot get depool address for '$DEPOOL_NAME'."
    exit 1
fi

# Check msig address
MSIG_addr="$(cat "${KEYS_DIR}/${MSIG_NAME}.addr" 2>/dev/null)"
if [[ -z "$MSIG_addr" ]]; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot find MSIG address in '${KEYS_DIR}/${MSIG_NAME}.addr'."
    exit 1
fi

# Check msig keys
msig_public="$(jq -r '.public' "${KEYS_DIR}/${MSIG_NAME}_1.keys.json" 2>/dev/null)"
msig_secret="$(jq -r '.secret' "${KEYS_DIR}/${MSIG_NAME}_1.keys.json" 2>/dev/null)"
if [[ -z "$msig_public" ]] || [[ -z "$msig_secret" ]]; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find SRC keypair file ('${KEYS_DIR}/${MSIG_NAME}_1.keys.json') in the keys directory."
    echo "   Ensure that you have at least 1 keypair file to sign transactions."
    exit 1
fi
echo "MSIG address: $MSIG_addr"

#===========================================================
# If the access mode is set to console and the node is not synchronized, prompt the user to switch to dapp mode temporarily
if Ask_to_switch_to_dapp; then
    echo "Proceeding with dApp mode..."
    export FORCE_USE_DAPP=true
elif [[ $? -gt 1 ]]; then
    echo "Operation cancelled by user."
    exit 1
fi

#================================================================
# Get custodians info
if Custodians_Info=$(Get_Account_Custodians_Info "$MSIG_addr"); then
    read -r Total_Custodians Required_Signs <<< "$Custodians_Info"
    echo "---INFO: Total msig custodians: $Total_Custodians; Required signs: $Required_Signs"
else
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot get custodians info for '$MSIG_NAME'."
    exit 1
fi
export Required_Signs

#=================================================
# Get Msig balance
MSIG_INFO="$(Get_Account_Info "${MSIG_addr}")"
MSIG_Status=$(echo "$MSIG_INFO" | awk '{print $1}')
MSIG_AMOUNT=$(echo "$MSIG_INFO" | awk '{print $2}')     # nanotokens
MSIG_AMOUNT_Tk=$(echo "scale=3; $((MSIG_AMOUNT)) / 1000000000" | $CALL_BC) # tokens
echo "MSIG status: $MSIG_Status with balance on account: ${MSIG_AMOUNT_Tk} tokens"
if [[ "$MSIG_Status" != "Active" ]]; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): MSIG account '${MSIG_addr}' is not active. Cannot stake from it."
    exit 1
fi
if [[ $MSIG_AMOUNT -lt 1000000000 ]]; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): MSIG account '${MSIG_addr}' balance is less than 1 token. Cannot stake from it."
    exit 1
fi
export MSIG_AMOUNT MSIG_AMOUNT_Tk

#=================================================
# Get number of local keys
# shellcheck disable=SC2207
MSIG_KEY_FILES_ARRAY=($(ls "${KEYS_DIR}/${MSIG_NAME}"*.keys.json 2>/dev/null))
declare -i LocalKeysQty=${#MSIG_KEY_FILES_ARRAY[@]}
echo "---INFO: Local keys quantity: $LocalKeysQty"
if [[ $LocalKeysQty -lt $Required_Signs ]]; then
    echo "+++-WARNING(${SelfScriptName} line $LINENO): You have only $LocalKeysQty keyfiles, but $Required_Signs signatures are required."
    echo "   You may need to sign transactions remotely."
fi

#=================================================
# Check depool is ready for stake
echo "Depool address: $Depool_addr"
echo "Checking depool status..."
Depool_INFO="$(Get_Account_Info "${Depool_addr}")"
Depool_Status=$(echo "$Depool_INFO" | awk '{print $1}')
Depool_AMOUNT=$(echo "$Depool_INFO" | awk '{print $2}')
Depool_AMOUNT_Tk=$(echo "scale=3; $((Depool_AMOUNT)) / 1000000000" | $CALL_BC)
echo "Depool status: $Depool_Status with balance on account: ${Depool_AMOUNT_Tk} tokens"
if [[ "$Depool_Status" != "Active" ]]; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): DePool account '${Depool_addr}' is not active. Cannot stake to it."
    exit 1
fi

if ! Current_Depool_Info="$(Get_DP_Info "$Depool_addr")"; then
    echo
    echo "###-ERROR(${SelfScriptName} line $LINENO): Cannot get depool info for '$Depool_addr'."
    exit 1
fi
export Current_Depool_Info
#---------------------------------------
# Depool raw info:
# {
#   "poolClosed": false,
#   "minStake": "10000000000",
#   "validatorAssurance": "50000000000000",
#   "participantRewardFraction": "85",
#   "validatorRewardFraction": "15",
#   "balanceThreshold": "45928171497",
#   "validatorWallet": "0:96d8d9e02362b65a3056d578488c61f4bcc5d54b5bf5f7f68e7fd4d700321d77",
#   "proxies": [
#     "-1:b3cc18076650a85d375e04dece2e66af757a1fecb5afc31659ddb7f0e5b82b0b",
#     "-1:8057bfc7c6700d8da1cf0e1470bc90a13b01c65a0b216333598a57da6379ef31"
#   ],
#   "stakeFee": "500000000",
#   "retOrReinvFee": "40000000",
#   "proxyFee": "90000000"
# }
#---------------------------------------
echo -e "\nDepool raw info:"
jq . <<< "$Current_Depool_Info"
echo
PoolClosed=$(echo "$Current_Depool_Info" | jq -r '.poolClosed')
if [[ "$PoolClosed" == "false" ]]; then
    echo -e "${GreenBack}${BoldText}OPEN for participation!${NormText}"
elif [[ "$PoolClosed" == "true" ]]; then
    echo -e "${RedBlink}${BoldText}CLOSED!!! Cannot perform operations on a closed depool. All stakes should be returned to participants.${NormText}"
    exit 1
else
    echo "###-ERROR(${SelfScriptName} line $LINENO): Can't determine the Depool state! Cannot continue."
    exit 1
fi

# Check depool balanceThreshold not less than current depool self balance
Depool_Self_Balance=$($CALL_CLI -j run --boc "${INPL_ELECTIONS_WORK_DIR}/${Depool_addr##*:}.boc" --abi "$INPL_DePool_ABI" getDePoolBalance '{}' | jq -r '.value0 | tonumber') # nanotokens
Depool_BalanceThreshold=$(( $(echo "$Current_Depool_Info" | jq -r '.balanceThreshold | tonumber') - 2000000000 ))       # nanotokens

DEPOOL_STAKE_FEE_NANO=$(echo "$Current_Depool_Info" | jq -r '.stakeFee | tonumber') # nanotokens
export DEPOOL_STAKE_FEE_NANO

if [[ $Depool_Self_Balance -lt $Depool_BalanceThreshold ]]; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Depool balanceThreshold is less than current depool self balance."
    echo "    Depool balanceThreshold: $Depool_BalanceThreshold tokens and self balance: $Depool_Self_Balance tokens"
    echo -e "${BoldText}    The depool must replenish its self-balance; otherwise, it cannot operate and risks losing stake in the elector.${NormText}"
    echo -e "     You can replenish the depool self-balance by running this script: ${BoldText}./${SelfScriptName} replenish ${DEPOOL_NAME} ${MSIG_NAME} <amount>${NormText}"
    exit 1
fi
echo -e "${BoldText}REMEMBER: Depool CRITICAL_THRESHOLD is 10 tokens. If the depool balance is less than 10 tokens, the depool will be stuck and will not be able to operate at all.${NormText}"
Depool_Self_Balance_Tk=$(echo "scale=3; $((Depool_Self_Balance)) / 1000000000" | $CALL_BC)
Depool_BalanceThreshold_Tk=$(echo "scale=3; $((Depool_BalanceThreshold)) / 1000000000" | $CALL_BC)
echo "Depool balanceThreshold: ${Depool_BalanceThreshold_Tk} tokens"
echo "Depool self balance:     ${Depool_Self_Balance_Tk} tokens"
if [[ $Depool_Self_Balance -lt 12000000000 ]]; then
    echo -e "${RedBack}${BoldText}+++-ALARM: Depool self balance is less than 12 tokens. The depool may be stuck and will not be able to operate.${NormText}"
    echo -e "     You should replenish the depool self-balance by running this script: ${BoldText}./${SelfScriptName} replenish ${DEPOOL_NAME} ${MSIG_NAME} <amount>${NormText}"
    exit 1
fi

# Check depool proxy balances
dp_proxy0=$(echo "$Current_Depool_Info" | jq -r ".proxies[0]")
dp_proxy1=$(echo "$Current_Depool_Info" | jq -r ".proxies[1]")
Proxy0_Info="$(Get_Account_Info "${dp_proxy0}")"
Proxy1_Info="$(Get_Account_Info "${dp_proxy1}")"
Proxy0_Bal=$(echo "${Proxy0_Info}" | awk '{print $2}')      # nanotokens
Proxy1_Bal=$(echo "${Proxy1_Info}" | awk '{print $2}')      # nanotokens
Proxy0_Bal_Tk=$(echo "scale=3; $((Proxy0_Bal)) / 1000000000" | $CALL_BC)
Proxy1_Bal_Tk=$(echo "scale=3; $((Proxy1_Bal)) / 1000000000" | $CALL_BC)
echo "Depool proxy0: ${dp_proxy0} balance: ${Proxy0_Bal_Tk} tokens"
echo "Depool proxy1: ${dp_proxy1} balance: ${Proxy1_Bal_Tk} tokens"
declare -ir TOPUP_THRESHOLD=1920000000 # 1.92 tokens
TOPUP_THRESHOLD_Tk=$(echo "scale=3; $((TOPUP_THRESHOLD)) / 1000000000" | $CALL_BC)
if [[ $Proxy0_Bal -lt $TOPUP_THRESHOLD ]] || [[ $Proxy1_Bal -lt $TOPUP_THRESHOLD ]]; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Proxy balance is less than $TOPUP_THRESHOLD_Tk tokens."
    echo "    Proxy0 balance: ${Proxy0_Bal_Tk} tokens"
    echo "    Proxy1 balance: ${Proxy1_Bal_Tk} tokens"
    echo -e "${BoldText}    The proxy balance must be at least 2 tokens; otherwise, it cannot operate and risks losing stake in the elector.${NormText}"
    echo -e "     You should top up the proxy balance before staking to depool by transferring tokens to the proxy address."
    echo -e "     Use the script ${BoldText}./transfer_amount.sh ${MSIG_NAME} <proxy address> <amount>${NormText} for this."
    exit 1
fi

#============================================
# Get depool rounds info
Depool_Rounds_Info="$(Get_DP_Rounds "$Depool_addr")"
Curr_Rounds_Info="$(Rounds_Sorting_by_ID "$Depool_Rounds_Info")"
Prev_Round_Stake_nt=$(echo  "$Curr_Rounds_Info" | jq -r ".[0].stake") # nanotokens
Prev_DP_Round_ID=$(echo     "$Curr_Rounds_Info" | jq -r ".[0].id")
Prev_Round_P_QTY=$(echo     "$Curr_Rounds_Info" | jq -r ".[0].participantQty")
Curr_Round_Stake_nT=$(echo  "$Curr_Rounds_Info" | jq -r ".[1].stake") # nanotokens
Curr_DP_Round_ID=$(echo     "$Curr_Rounds_Info" | jq -r ".[1].id")
Curr_Round_P_QTY=$(echo     "$Curr_Rounds_Info" | jq -r ".[1].participantQty")
Prev_Round_Stake_Tk=$(echo "scale=3; $((Prev_Round_Stake_nt)) / 1000000000" | $CALL_BC)
Curr_Round_Stake_Tk=$(echo "scale=3; $((Curr_Round_Stake_nT)) / 1000000000" | $CALL_BC)
echo -e "\nDepool rounds info:"
echo "Previous round ${Prev_DP_Round_ID} stake: ${Prev_Round_Stake_Tk} tokens with ${Prev_Round_P_QTY} participants"
echo "Current  round ${Curr_DP_Round_ID} stake: ${Curr_Round_Stake_Tk} tokens with ${Curr_Round_P_QTY} participants"

# ===============================================================
# Check unsent transactions in MSIG account if more than 1 custodian
if [[ ${Required_Signs} -gt 1 ]]; then
    echo -e "\n--- INFO: Checking unsent transactions in MSIG account..."
    Trans_List="$(Get_MSIG_Trans_List "${MSIG_addr}")"
    # shellcheck disable=SC2155
    declare -i Trans_QTY=$(echo "${Trans_List}" | jq -r ".transactions | length")
    declare -i Exist_El_Trans_Qty=0
    declare -i Exist_DP_Trans_Qty=0
    declare -i Exist_Proxy0_Trans_Qty=0
    declare -i Exist_Proxy1_Trans_Qty=0
    if [[ ${Trans_QTY} -gt 0 ]]; then
        Exist_El_Trans_Qty=$(echo "${Trans_List}" | jq -r ".transactions[] | select(.dest == \"${elector_addr}\") | length")
        if [[ "$STAKE_MODE" == "depool" ]]; then
            Exist_DP_Trans_Qty=$(echo "${Trans_List}"     | jq -r ".transactions[] | select(.dest == \"${Depool_addr}\") | length")
            Exist_Proxy0_Trans_Qty=$(echo "${Trans_List}" | jq -r ".transactions[] | select(.dest == \"${dp_proxy0}\") | length")
            Exist_Proxy1_Trans_Qty=$(echo "${Trans_List}" | jq -r ".transactions[] | select(.dest == \"${dp_proxy1}\") | length")
        fi
        echo -e "${BoldText}+++-WARNING(${SelfScriptName} line $LINENO): You have unsigned transactions on the MSIG account!${NormText}"
        echo "  Transactions to Elector: $Exist_El_Trans_Qty"
        echo "  Transactions to DePool:  $Exist_DP_Trans_Qty"
        echo "  Transactions to Proxy0:  $Exist_Proxy0_Trans_Qty"
        echo "  Transactions to Proxy1:  $Exist_Proxy1_Trans_Qty"
    fi
    {
        date +'%F %T %Z'
        printf "Total transactions qty:      %s\n" "${Trans_QTY}"
        printf "To Elector transactions qty: %s\n" "${Exist_El_Trans_Qty}"
        printf "To DePool transactions qty:  %s\n" "${Exist_DP_Trans_Qty}"
        printf "To Proxy0 transactions qty:  %s\n" "${Exist_Proxy0_Trans_Qty}"
        printf "To Proxy1 transactions qty:  %s\n" "${Exist_Proxy1_Trans_Qty}"
        echo
    } | tee -a "${ELECTIONS_WORK_DIR}/unsent_transactions.log"
fi

if [[ $Trans_QTY -ge 5 ]]; then
    echo -e "${RedBack}${BoldText}+++-ALARM: You have reached the maximum number of unsigned transactions on the MSIG account."
    echo -e "  You should sign and send them before staking to depool.${NormText}"
    exit 1
fi

#===========================================================
# TODO: Check the depool stake in the network and compare with other stakes in the network

#===========================================================
case $STAKE_OP in
    "ordinary")
        echo "Making ordinary stake..."
        if [[ $# -ne 1 ]]; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Incorrect number of parameters for ordinary stake."
            Usage
        fi
        STAKE_AMOUNT="$1"
        source "${SCRIPT_DIR}/depool_functions/ordinary_stake.shinc"
        if ! make_ordinary_stake "${Depool_addr}" "${MSIG_NAME}" "${STAKE_AMOUNT}"; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Error making ordinary stake."
            exit 1
        fi
        ;;
    "replenish")
        echo "Replenishing depool self balance..."
        if [[ $# -ne 1 ]]; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Incorrect number of parameters for replenishing depool self balance."
            Usage
        fi
        STAKE_AMOUNT="$1"
        source "${SCRIPT_DIR}/depool_functions/replenish_depool.shinc"
        if ! replenish_depool "${Depool_addr}" "${MSIG_NAME}" "${STAKE_AMOUNT}"; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Error replenishing depool self balance."
            exit 1
        fi
        ;;
    "lock-donor")
        echo "Setting donor for lock stake in the depool for '$MSIG_NAME'..."
        if [[ $# -ne 1 ]]; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Incorrect number of parameters for setting lock stake donor."
            Usage
        fi
        DONOR_NAME="$1"
        source "${SCRIPT_DIR}/depool_functions/set_lock_donor.shinc"
        if ! set_msig_lock_donor "${Depool_addr}" "${MSIG_NAME}" "${DONOR_NAME}"; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Error setting donor for lock stake."
            exit 1
        fi
        ;;
    "vesting-donor")
        echo "Setting donor for vesting stake in the depool for '$MSIG_NAME'..."
        if [[ $# -ne 1 ]]; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Incorrect number of parameters for setting vesting stake donor."
            Usage
        fi
        DONOR_NAME="$1"
        source "${SCRIPT_DIR}/depool_functions/set_vesting_donor.shinc"
        if ! set_msig_vesting_donor "${Depool_addr}" "${MSIG_NAME}" "${DONOR_NAME}"; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Error setting donor for vesting stake."
            exit 1
        fi
        ;;
    "vesting")
        echo "Making vesting stake..."
        if [[ $# -ne 4 ]]; then
            echo "###-ERROR(${SelfScriptName} line $LINENO): Incorrect number of parameters for vesting stake."
            Usage
        fi
        STAKE_AMOUNT="$1"
        STAKE_TOTAL_TIME="$2"
        STAKE_WITHDRAWAL_TIME="$3"
        STAKE_BENEFICIARY="$4"
        source "${SCRIPT_DIR}/depool_functions/vesting_stake.shinc"
        if ! make_vesting_stake "$Depool_addr" "$MSIG_NAME" "$STAKE_AMOUNT" "$STAKE_TOTAL_TIME" "$STAKE_WITHDRAWAL_TIME" "$STAKE_BENEFICIARY"; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Error making vesting stake."
            exit 1
        fi
        ;;
    "lock")
        echo "Making lock stake..."
        if [[ $# -ne 4 ]]; then
            echo "###-ERROR(${SelfScriptName} line $LINENO): Incorrect number of parameters for lock stake."
            Usage
        fi
        STAKE_AMOUNT="$1"
        STAKE_TOTAL_TIME="$2"
        STAKE_WITHDRAWAL_TIME="$3"
        STAKE_BENEFICIARY="$4"
        source "${SCRIPT_DIR}/depool_functions/lock_stake.shinc"
        if ! make_lock_stake "$Depool_addr" "$MSIG_NAME" "$STAKE_AMOUNT" "$STAKE_TOTAL_TIME" "$STAKE_WITHDRAWAL_TIME" "$STAKE_BENEFICIARY"; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Error making lock stake."
            exit 1
        fi
        ;;
    "WD-all")
        echo "Withdrawing all stake from all rounds..."
        if [[ $# -ne 0 ]]; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): 'WD-all' operation does not take any additional parameters."
            Usage
        fi
        source "${SCRIPT_DIR}/depool_functions/withdraw_entire_stake.shinc"
        if ! withdraw_entire_stake "$Depool_addr" "$MSIG_NAME"; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Error withdrawing all stake."
            exit 1
        fi
        ;;
    "WD-part")
        echo "Withdrawing part of the stake from the next round..."
        if [[ $# -ne 1 ]]; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Incorrect number of parameters for withdrawing part of the stake."
            Usage
        fi
        STAKE_AMOUNT="$1"
        source "${SCRIPT_DIR}/depool_functions/withdraw_part_stake.shinc"
        if ! withdraw_part_stake "$Depool_addr" "$MSIG_NAME" "$STAKE_AMOUNT"; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Error withdrawing part of the stake."
            exit 1
        fi
        ;;
    "cancelWithdrawal")
        echo "Cancelling withdrawal stake and setting reinvestment on..."
        if [[ $# -ne 0 ]]; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): 'cancelWithdrawal' operation does not take any additional parameters."
            Usage
        fi
        source "${SCRIPT_DIR}/depool_functions/cancel_withdrawal.shinc"
        if ! cancel_withdrawal "$Depool_addr" "$MSIG_NAME"; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Error cancelling withdrawal stake."
            exit 1
        fi
        ;;
    "remove-stake")
        echo "Removing an ordinary stake from a pooling round..."
        if [[ $# -ne 1 ]]; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): 'remove-stake' operation needs only one parameter - STAKE_AMOUNT."
            Usage
        fi
        STAKE_AMOUNT="$1"
        source "${SCRIPT_DIR}/depool_functions/remove_stake.shinc"
        if ! remove_stake "$Depool_addr" "$MSIG_NAME" "$STAKE_AMOUNT"; then
            echoerr "###-ERROR(${SelfScriptName} line $LINENO): Error removing stake."
            exit 1
        fi
        ;;
    *)
        echo "###-ERROR: Unknown operation: '$STAKE_OP'."
        Usage
        ;;
esac

exit 0
