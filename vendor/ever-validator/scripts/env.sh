#!/usr/bin/env bash
#shellcheck source=text_mods.shinc

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
export ENV_LOADED=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
NODE_TOP_DIR="$(cd "${SCRIPT_DIR}/../" && pwd -P)" && export NODE_TOP_DIR
source "${SCRIPT_DIR}/text_mods.shinc"

#=====================================================
# Network related variables
export NETWORK_TYPE="devnet"            # can be main|mainnet / net|devnet 
export RUN_MODE="docker"                # can be 'docker' or 'service'  or 'offline'
export DOCKER_USE_CUSTOM_IMAGE=false    # To use custom docker image builded locally set it to true otherwise to use official image set it to false
export NODE_WC=0                        # Node WorkChain

FORCE_USE_DAPP=false                    # For offnode works or to use DApp Server instead of use node's console to operate
[[ "${RUN_MODE}" == "offline" ]] && FORCE_USE_DAPP=true  # For offline mode we need to use DApp server anyway
export FORCE_USE_DAPP
export STAKE_MODE="depool"              # can be 'msig' or 'depool'
export NODE_ROLE="validator"            # can be 'sync' or 'validator' or 'dapp'
export VALIDATOR_NAME="${HOSTNAME%%.*}"
export CRONTAB_INTERVAL=10              # Interval for crontab in minutes to check and take part in elections 
#=====================================================
# Depool deploy defaults
export DePool_TYPE="EverX"              # can be 'EverX' or 'StEver'
export ValidatorAssuranceT=50000        # Assurance in tokens
export MinStakeT=10                     # Min DePool assepted stake in tokens
export ParticipantRewardFraction=85     # In % participant share from reward
export BalanceThresholdT=20             # Min depool self balance to operate
export TIK_REPLANISH_AMOUNT=10          # If Tik acc balance less 2 tokens, It will be auto topup with this amount

#=====================================================
# Msig validation defaults
export MSIG_FIX_STAKE=0                 # fixed stake for 'msig' mode (tokens). if 0 - use whole stake
export VAL_ACC_INIT_BAL=1200000         # Initial balance on validator account for full balance staking (if MSIG_FIX_STAKE=0)
export VAL_ACC_RESERVED=50              # Reserved amount staying on msig account in full staking mode
export MAX_FACTOR=3                     # Max factor for stake calculation in Elector contract 
export DELAY_TIME=0                     # Delay time from the start of elections
export TIME_SHIFT=300                   # Time between sequential scripts
export LC_Send_MSG_Timeout=10           # time after Lite-Client send message to BC in seconds

#=====================================================
# AUTO UPDATE Settings
export newReleaseSndMsg=true            # Send message to Telegram about new release
export Enable_Node_Autoupdate=true      # will automatically update node, tools, ever-cli etc..
export Enable_Scripts_Autoupdate=true   # Updating scripts. NB! 
# Last Node Info Contract for safe node auto updates
# export LNIC_ADDRESS="0:bdcefecaae5d07d926f1fa881ea5b61d81ea748bd02136c0dbe76604323fc347"
export LNIC_ADDRESS="0:68ab2b8f520fc9bee677320ffafb8363e2872a68010c0b717aca62485fc73cfa"

#=====================================================
# Telegram bot settings
export TELEGRAM_BOT_TOKEN=""           # Telegram bot token
export TELEGRAM_CHAT_ID=""             # Telegram chat ID

#=====================================================
# DApp keys
export DAPP_Project_id=""                       # from 2022.09.09 needs for DApp access (man - https://docs.everos.dev/evernode-platform/products/evercloud/get-started)
export DAPP_access_key=""                       #
export Auth_key_Head="Authorization: Basic "    # header for curl: -H "${Auth_key_Head}"
export ipi_token=""                             # token for ipinfo.io

#=====================================================
# Docker related variables
# Use official EverX docker image or build your own
export DOCKERHUB_USER="everx"            # DockerHub username
export DOCKERHUB_REPO="ever-node"        # DockerHub repository
export DOCKER_IMAGE_TAG="latest"         # Official EverX docker images https://hub.docker.com/u/everx
export DOCKER_IMAGE_REPO="$DOCKERHUB_USER/$DOCKERHUB_REPO"        # Official EverX docker images https://hub.docker.com/u/everx
export STATSD_DOMAIN="localhost"
export STATSD_UDP_PORT=9125
export STATSD_TCP_PORT=9102
export STATSD_EXTERNAL_IP="127.0.0.1"   # or "${NODE_IP_ADDR}"
export PROMETHEUS_PORT=9090
# IP and port for services
export NODE_IP_ADDR="should be set in Initial_Setup.sh"  # External IP address of the node will set in Initial_Setup.sh or set it manually
export ADNL_PORT="58888"
export NODE_ADDRESS="${NODE_IP_ADDR}:${ADNL_PORT}"
export RCONSOLE_PORT="5888"

###################################################
###################################################
# Check if the environment is configured
# You must set this variable to true after configuring the environment
# if you are using this script for the first time.
# This step is required even if you do not wish to make any changes to this script.
# It ensures that you have reviewed and acknowledged the environment configuration settings.
IS_ENVIRONMENT_CONFIGURED=false
if ! $IS_ENVIRONMENT_CONFIGURED; then
    echo -e "\n${RedBack}${BoldText}${Tg_Error_sign} ###-ERROR(line $LINENO): The environment is not configured!${NormText}\n"
    echo -e "${BoldText}You must set the 'IS_ENVIRONMENT_CONFIGURED' variable to true in 'scripts/env.sh' after reviewing and configuring the environment settings.\n"
    echo -e "This step is crucial to ensure that all environmental configurations are correctly acknowledged and set up before using this script. \n"
    echo -e "This requirement applies even if no changes are made to the script. \n"
    echo -e "It serves as a confirmation that you have carefully checked the environment configuration.${NormText}\n"
    if [[ "$(basename "$0")" == "bash" || "$(basename "$0")" == "-bash" ]]; then return 1; else exit 1; fi
fi
###################################################
###################################################

#=====================================================
# Networks endpoints
export Main_DApp_URL="https://mainnet.evercloud.dev"
export MainNet_DApp_List="https://mainnet.evercloud.dev,https://gra01.main.everos.dev,https://lim01.main.everos.dev"

export DevNet_DApp_URL="https://net.evercloud.dev"
export DevNet_DApp_List="https://net.evercloud.dev,https://eri01.net.everos.dev,https://gra01.net.everos.dev"

#=====================================================
# Nets zeroblock root hashes
export MAIN_NET_RH="95f042d1bf5b99840cad3aaa698f5d7be13d9819364faf9dd43df5b5d3c2950e"
export MAIN_NET_ROOT_HASH="WP/KGheNr/cF3lQhblQzyb0ufYUAcNM004mXhHq56EU="
export  DEV_NET_RH="967321a1e0773c7856e0b686f4b1225b6fd90d1ad3007435de3dc7dd4081494e"
export  DEV_NET_ROOT_HASH="zYHa4MI9eOfD61kD8qe9mIiZkdNqJoEqkWPKDynEcJM="

#=====================================================
# Versions
export Node_Blk_Min_Ver=59
export RUST_VERSION="1.81.0"
export YQ_VERSION="4.44.5"
export MIN_CLI_VERSION="0.40.0"

#=====================================================
# GIT addresses & commits
export NODE_GIT_REPO="https://github.com/everx-labs/ever-node.git"
export NODE_GIT_COMMIT="master"
export NODE_BUILD_FEATURES="statsd"

export CLI_GIT_REPO="https://github.com/everx-labs/ever-cli.git"
export CLI_GIT_COMMIT="master"

export TVM_LINKER_GIT_REPO="https://github.com/everx-labs/TVM-linker.git"
export TVM_LINKER_GIT_COMMIT="master"

export SOLC_GIT_REPO="https://github.com/everx-labs/TVM-Solidity-Compiler.git"
export SOLC_GIT_COMMIT="master"

export CONTRACTS_GIT_REPO="https://github.com/everx-labs/ton-labs-contracts.git"
export CONTRACTS_GIT_COMMIT="master"

#=====================================================
# Names
export DOCKER_NODE_CONTAINER_NAME="ever-node"
export NET_GLOBAL_CFG_FILE_NAME="ton-global.config.json"
export NODE_BIN_NAME="ever-node"
export CLI_BIN_NAME="ever-cli"
export CLI_CONF_FILE="ever-cli.conf.json"

#================================================================
# Set switch to inner docker mode
USE_IN_DOCKER_CONTEXT=false
if [[ $RUN_MODE == "docker" ]] && [[ ! -f /.dockerenv ]]; then
    if [[ -z $INITIAL_SETUP ]] && [[ ! $(docker ps -q -f name=${DOCKER_NODE_CONTAINER_NAME}) ]]; then
        echo "###-ERROR: ENV.SH ${LINENO}: RUN_MODE is '$RUN_MODE' but container  ${DOCKER_NODE_CONTAINER_NAME} is not running! Can't recognize run context!" 
        # Check if script is sourced or executed
        if [[ "$(basename "$0")" == "bash" || "$(basename "$0")" == "-bash" ]]; then return 1; else exit 1; fi
    fi
    USE_IN_DOCKER_CONTEXT=true
fi
export USE_IN_DOCKER_CONTEXT

#=====================================================
# Paths
export CONFIGS_DIR=${NODE_TOP_DIR}/configs
INPL_CONFIGS_DIR="${CONFIGS_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_CONFIGS_DIR="/ever-node/configs"; export INPL_CONFIGS_DIR
export KEYS_DIR="${NODE_TOP_DIR}/keys"
INPL_KEYS_DIR="${KEYS_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_KEYS_DIR="/ever-node/keys"; export INPL_KEYS_DIR

#=====================================================
# Source code folders
export SOURCES_DIR="${NODE_TOP_DIR}/src"
export NODE_SRC_DIR="${SOURCES_DIR}/ever-node"
export CLI_SRC_DIR="${SOURCES_DIR}/ever-cli"
export TVM_LINKER_SRC_DIR="${SOURCES_DIR}/TVM_Linker"
export SOLC_SRC_DIR="${SOURCES_DIR}/SolC"
export CRYPTO_DIR=$SOURCES_DIR/crypto
export DOCKER_NODE_DIR="${NODE_TOP_DIR}/docker/ever-node"
export DOCKER_NODE_BUILD_DIR="${DOCKER_NODE_DIR}/build"
export DOCKER_NODE_ENV_FILE="${NODE_TOP_DIR}/docker/ever-node/.env"
export DOCKER_STATSD_DIR="${NODE_TOP_DIR}/docker/statsd"
export DOCKER_STATSD_ENV_FILE="${NODE_TOP_DIR}/docker/statsd/.env"

#=====================================================
# Node database, configs and logs folders
export NODE_CFG_DIR="${NODE_TOP_DIR}/node_cfg"
INPL_NODE_CFG_DIR="${NODE_CFG_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_NODE_CFG_DIR="/ever-node/node_cfg"; export INPL_NODE_CFG_DIR
export NODE_DB_DIR="${NODE_TOP_DIR}/node_db"
INPL_NODE_DB_DIR="${NODE_DB_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_NODE_DB_DIR="/ever-node/node_db"; export INPL_NODE_DB_DIR
export EVER_LOG_DIR="${NODE_TOP_DIR}/logs"
INPL_EVER_LOG_DIR="${EVER_LOG_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_EVER_LOG_DIR="/ever-node/logs"; export INPL_EVER_LOG_DIR
export NODE_LOG_DIR="${EVER_LOG_DIR}/node"
INPL_NODE_LOG_DIR="${NODE_LOG_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_NODE_LOG_DIR="/ever-node/logs/node"; export INPL_NODE_LOG_DIR
export NODE_LOG_FILE="node.log"
# Keep node log files after logrotate in separate folder for X days
export NODE_LOGS_ARCH="${EVER_LOG_DIR}/archives"
INPL_NODE_LOGS_ARCH="${NODE_LOGS_ARCH}"; $USE_IN_DOCKER_CONTEXT && INPL_NODE_LOGS_ARCH="/ever-node/logs/archives"; export INPL_NODE_LOGS_ARCH
export NODE_LOGs_ARCH_KEEP_DAYS=5

#=====================================================
# Log folders and files
export VALIDATOR_LOG_FILE_NAME="validator.log"
export VALIDATOR_LOG_DIR="${EVER_LOG_DIR}/validator"
export ELECTIONS_WORK_DIR="${NODE_TOP_DIR}/elections"
INPL_ELECTIONS_WORK_DIR="${ELECTIONS_WORK_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_ELECTIONS_WORK_DIR="/ever-node/elections"; export INPL_ELECTIONS_WORK_DIR
export ELECTIONS_HISTORY_DIR="${ELECTIONS_WORK_DIR}/elections_hist"
INPL_ELECTIONS_HISTORY_DIR="${ELECTIONS_HISTORY_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_ELECTIONS_HISTORY_DIR="/ever-node/elections/elections_hist"; export INPL_ELECTIONS_HISTORY_DIR

#=====================================================
# Smart contracts paths
export ContractsDIR="${NODE_TOP_DIR}/contracts"
INPL_ContractsDIR="${ContractsDIR}"; $USE_IN_DOCKER_CONTEXT && INPL_ContractsDIR="/ever-node/contracts"; export INPL_ContractsDIR
export Elector_ABI="${ContractsDIR}/EverX/elector/Elector.abi.json"
INPL_Elector_ABI="${Elector_ABI}"; $USE_IN_DOCKER_CONTEXT && INPL_Elector_ABI="/ever-node/contracts/EverX/elector/Elector.abi.json"; export INPL_Elector_ABI
#=====================================================
# Set depool dir based on DePool type
case "${DePool_TYPE}" in
    "EverX")
        export DSCs_DIR="${ContractsDIR}/EverX/depool"
        INPL_DSCs_DIR="${DSCs_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_DSCs_DIR="/ever-node/contracts/EverX/depool"; export INPL_DSCs_DIR
        ;;
    "StEver")
        export DSCs_DIR="${ContractsDIR}/Broxus/StEver"
        INPL_DSCs_DIR="${DSCs_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_DSCs_DIR="/ever-node/contracts/Broxus/StEver"; export INPL_DSCs_DIR
        ;;
    *)
        echo -e "${RedBack}${BoldText}${Tg_Exclaim_sign} ###-ERROR(line $LINENO): Unknown DePool type!${NormText}"
        if [[ "$(basename "$0")" == "bash" || "$(basename "$0")" == "-bash" ]]; then return 1; else exit 1; fi
        ;;
esac

export DePool_ABI="$DSCs_DIR/DePool.abi.json"
INPL_DePool_ABI="${DePool_ABI}"; $USE_IN_DOCKER_CONTEXT && INPL_DePool_ABI="$INPL_DSCs_DIR/DePool.abi.json"; export INPL_DePool_ABI
export DePool_TVC="$DSCs_DIR/DePool.tvc"
INPL_DePool_TVC="${DePool_TVC}"; $USE_IN_DOCKER_CONTEXT && INPL_DePool_TVC="$INPL_DSCs_DIR/DePool.tvc"; export INPL_DePool_TVC

#=====================================================
# FIFT
export FSCs_DIR="${CRYPTO_DIR}/smartcont"
export FIFT_LIB="${CRYPTO_DIR}/fift/lib"

#=====================================================
# Multisig wallet contracts paths
export SafeSCs_DIR="${ContractsDIR}/EverX/safemultisig"
INPL_SafeSCs_DIR="${SafeSCs_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_SafeSCs_DIR="/ever-node/contracts/EverX/safemultisig"; export INPL_SafeSCs_DIR
export SafeC_Wallet_ABI="${SafeSCs_DIR}/SafeMultisigWallet.abi.json"
INPL_SafeC_Wallet_ABI="${SafeC_Wallet_ABI}"; $USE_IN_DOCKER_CONTEXT && INPL_SafeC_Wallet_ABI="$INPL_SafeSCs_DIR/SafeMultisigWallet.abi.json"; export INPL_SafeC_Wallet_ABI

export SetSCs_DIR="${ContractsDIR}/EverX/setcodemultisig"
INPL_SetSCs_DIR="${SetSCs_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_SetSCs_DIR="/ever-node/contracts/EverX/setcodemultisig"; export INPL_SetSCs_DIR
export SetC_Wallet_ABI="${SetSCs_DIR}/SetcodeMultisigWallet.abi.json"
INPL_SetC_Wallet_ABI="${SetC_Wallet_ABI}"; $USE_IN_DOCKER_CONTEXT && INPL_SetC_Wallet_ABI="$INPL_SetSCs_DIR/SetcodeMultisigWallet.abi.json"; export INPL_SetC_Wallet_ABI

export SurfSCs_DIR="${ContractsDIR}/EverX/Surf-contracts"
INPL_SurfSCs_DIR="${SurfSCs_DIR}"; $USE_IN_DOCKER_CONTEXT && INPL_SurfSCs_DIR="/ever-node/contracts/EverX/Surf-contracts"; export INPL_SurfSCs_DIR
export SURF_ABI="${SurfSCs_DIR}/setcodemultisig/SetcodeMultisigWallet.abi.json"
INPL_SURF_ABI="${SURF_ABI}"; $USE_IN_DOCKER_CONTEXT && INPL_SURF_ABI="$INPL_SurfSCs_DIR/setcodemultisig/SetcodeMultisigWallet.abi.json"; export INPL_SURF_ABI

#=====================================================
# Executables
export NODE_BIN_DIR=/usr/local/bin
EXECUTE_BINARIES_PATH="${NODE_BIN_DIR}"; $USE_IN_DOCKER_CONTEXT && EXECUTE_BINARIES_PATH="docker exec ${DOCKER_NODE_CONTAINER_NAME} ${NODE_BIN_DIR}"; export EXECUTE_BINARIES_PATH

export CALL_NODE="${EXECUTE_BINARIES_PATH}/${NODE_BIN_NAME} --configs ${INPL_NODE_CFG_DIR}"
export CALL_NODE_KAFKA="${EXECUTE_BINARIES_PATH}/${NODE_BIN_NAME}-kafka --configs ${INPL_NODE_CFG_DIR}"
export CALL_CONS="${EXECUTE_BINARIES_PATH}/console -C ${INPL_NODE_CFG_DIR}/console.json"
export CALL_CLI="${EXECUTE_BINARIES_PATH}/${CLI_BIN_NAME} -c ${INPL_NODE_CFG_DIR}/${CLI_CONF_FILE}"
export CALL_KEYGEN="${EXECUTE_BINARIES_PATH}/keygen"

OS_SYSTEM=$(uname -s) && export OS_SYSTEM
if [[ "$OS_SYSTEM" == "Linux" ]];then
    export CALL_BC="bc"
else
    export CALL_BC="bc -l"
fi
export CALL_7Z="7za"
function echoerr() { printf "\e[31;1m%b\e[0m\n" "$*" >&2; }
export ENV_LOADED=true
