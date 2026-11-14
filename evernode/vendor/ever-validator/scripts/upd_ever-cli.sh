#!/usr/bin/env bash
# shellcheck source=env.sh
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

BUILD_STRT_TIME=$(date +%s)
echo
echo "################################## CLI build script ############################################"
echo "+++INFO: $(basename "$0") BEGIN $(date +%s) / $(date)"
SelfScriptName=$(basename "$0")
echo "--- INFO: ${SelfScriptName} BEGIN $(date +%s) / $(date  +'%F %T %Z')"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
if ! source "${SCRIPT_DIR}/env.sh"; then
    echo "###-ERROR(${SelfScriptName}: line $LINENO): Can't load env.sh"
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

#=====================================================
# Install or upgrade RUST
declare -i Curr_Rust_Ver_NUM ENV_Rust_Ver_NUM
Curr_Rust_Ver_NUM=$(rustc -V | awk '{print $2}'| awk -F'.' '{printf("%d%03d%03d\n", $1,$2,$3)}')
ENV_Rust_Ver_NUM=$(echo $RUST_VERSION | awk -F'.' '{printf("%d%03d%03d\n", $1,$2,$3)}')
if [[ $Curr_Rust_Ver_NUM -lt $ENV_Rust_Ver_NUM ]];then
    echo
    echo '################################################'
    echo "---INFO: Install RUST ${RUST_VERSION}"
    cd "$HOME" || { echo "###-ERROR(line $LINENO): Can't change directory to $HOME"; exit 1; }
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- --default-toolchain ${RUST_VERSION} -y
    source "$HOME/.cargo/env"
    cargo install cargo-binutils
fi 

#=====================================================
# Build CLI binary
[[ -n ${CLI_SRC_DIR} ]] && rm -rf "${CLI_SRC_DIR:?}"
git clone --recurse-submodules "${CLI_GIT_REPO}" "${CLI_SRC_DIR}"
cd "${CLI_SRC_DIR}" || { echo "###-ERROR(line $LINENO): Can't change directory to ${CLI_SRC_DIR}"; exit 1; }
git checkout "${CLI_GIT_COMMIT}"
git submodule init && git submodule update --recursive
git submodule foreach 'git submodule init'
git submodule foreach 'git submodule update  --recursive'

echo -e "${BoldText}${BlueBack}---INFO: CLI git repo:   ${CLI_GIT_REPO} ${NormText}"
echo -e "${BoldText}${BlueBack}---INFO: CLI git commit: ${CLI_GIT_COMMIT} ${NormText}"

cargo update
cargo build --release
sudo cp -f "${CLI_SRC_DIR}/target/release/ever-cli" "${NODE_BIN_DIR}/${CLI_BIN_NAME}"

echo
"${NODE_BIN_DIR}/${CLI_BIN_NAME}" version
echo
BUILD_END_TIME=$(date +%s)
Build_mins=$(( (BUILD_END_TIME - BUILD_STRT_TIME)/60 ))
Build_secs=$(( (BUILD_END_TIME - BUILD_STRT_TIME)%60 ))
echo "+++INFO: $(basename "$0") FINISHED $(date +%s) / $(date)"
echo "Builds took $Build_mins min $Build_secs secs"
echo "================================================================================================"

exit 0
