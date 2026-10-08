#!/usr/bin/env bash
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

# All generated executables will be placed in the $NODE_BIN_DIR folder.
# Options:
#  rust - build rust node with utils
#  dapp - build rust node with utils for DApp server. 

BUILD_STRT_TIME=$(date +%s)

echo
echo "################################### Everscale nodes build script ###################################"
SelfScriptName=$(basename "$0") && export SelfScriptName
echo "INFO: $SelfScriptName BEGIN $(date +%s) / $(date +'%F %T %Z')"
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

BackUP_Time="$(date +%Y-%m-%d_%H-%M-%S)"

[[ ! -d "${NODE_BIN_DIR}" ]] && mkdir -p "${NODE_BIN_DIR}"

#=====================================================
# Packages set for different OSes
PKGS_FreeBSD="git mc jq vim 7-zip libtool perl5 automake llvm-devel gmake wget gawk base64 cmake curl gperf openssl ca_root_nss lzlib sysinfo logrotate zstd pkgconf python google-perftools"
PKGS_CentOS="git  mc jq vim bc p7zip curl wget libtool logrotate openssl-devel clang llvm-devel cmake gperf gawk zlib zlib-devel bzip2 bzip2-devel lz4-devel libzstd-devel gperftools gperftools-devel"
PKGS_Ubuntu="git  mc jq vim bc p7zip-full curl build-essential libssl-dev automake libtool clang llvm-dev cmake gawk gperf libz-dev pkg-config zlib1g-dev libzstd-dev libgoogle-perftools-dev"
PKGS_OL9UEK="git  mc jq vim bc p7zip curl wget libtool logrotate openssl-devel clang llvm-devel cmake gperf gawk zlib zlib-devel bzip2 bzip2-devel lz4-devel libzstd-devel libunwind libunwind-devel"

PKG_MNGR_FreeBSD="sudo pkg"
PKG_MNGR_CentOS="sudo dnf"
PKG_MNGR_Ubuntu="sudo apt"
FEXEC_FLG="-executable"

#=====================================================
# Detect OS 
OS_SYSTEM=$(uname -s)
OS_DISTRO=$OS_SYSTEM
if [[ "$OS_DISTRO" == "Linux" ]];then
    OS_DISTRO="$(hostnamectl |grep 'Operating System'|awk '{print $3}')"
elif [[ ! "$OS_DISTRO" == "FreeBSD" ]];then
    echo
    echo "###-ERROR(line $LINENO): Unknown or unsupported OS. Can't continue."
    echo
    exit 1
fi

#=====================================================
# Set packages set & manager according to OS
case "$OS_DISTRO" in
    FreeBSD)
        export ZSTD_LIB_DIR=/usr/local/lib
        PKGs_SET=$PKGS_FreeBSD
        PKG_MNGR=$PKG_MNGR_FreeBSD
        $PKG_MNGR update -f
        $PKG_MNGR upgrade -y
        FEXEC_FLG="-perm +111"
        ;;
    CentOS)
        export ZSTD_LIB_DIR=/usr/lib64
        PKGs_SET=$PKGS_CentOS
        PKG_MNGR=$PKG_MNGR_CentOS
        $PKG_MNGR -y update --allowerasing
        $PKG_MNGR group install -y "Development Tools"
        $PKG_MNGR config-manager --set-enabled powertools 
        $PKG_MNGR --enablerepo=extras install -y epel-release
        sudo systemctl daemon-reload
        ;;
    Oracle)
        export ZSTD_LIB_DIR=/usr/lib64
        PKGs_SET=$PKGS_CentOS
        PKG_MNGR=$PKG_MNGR_CentOS
        $PKG_MNGR -y update --allowerasing
        $PKG_MNGR group install -y "Development Tools"
        if grep -q 'VERSION_ID="9.' /etc/os-release ;then
            PKGs_SET=$PKGS_OL9UEK
            $PKG_MNGR config-manager --set-enabled ol9_codeready_builder
            $PKG_MNGR install -y oracle-epel-release-el9
        else 
            $PKG_MNGR config-manager --set-enabled ol8_codeready_builder
            $PKG_MNGR install -y oracle-epel-release-el8
        fi
        sudo systemctl daemon-reload
        ;;
    Fedora|Rocky|Red)
        export ZSTD_LIB_DIR=/usr/lib64
        PKGs_SET=$PKGS_CentOS
        PKG_MNGR=$PKG_MNGR_CentOS
        $PKG_MNGR -y update --allowerasing
        $PKG_MNGR group install -y "Development Tools"
        sudo systemctl daemon-reload
        ;;
    Ubuntu|Debian)
        export ZSTD_LIB_DIR=/usr/lib/x86_64-linux-gnu
        PKGs_SET=$PKGS_Ubuntu
        PKG_MNGR=$PKG_MNGR_Ubuntu
        $PKG_MNGR install -y software-properties-common
        sudo add-apt-repository -y ppa:ubuntu-toolchain-r/ppa
        sudo systemctl daemon-reload
        ;;
    *)
        echo
        echo "###-ERROR(line $LINENO): Unknown or unsupported OS. Can't continue."
        echo
        exit 1
        ;;
esac

#=====================================================
# Install packages
echo
echo '################################################'
echo "---INFO: Install packages ... "
# shellcheck disable=SC2086
$PKG_MNGR install -y $PKGs_SET

if grep -q 'PRETTY_NAME="Oracle Linux Server 9' /etc/os-release && [[ ! -d "/usr/local/share/doc/gperftools" ]];then
    mkdir -p ~/src && cd ~/src
    git clone --recursive https://github.com/gperftools/gperftools.git
    cd gperftools
    ./autogen.sh && ./configure && make && sudo make install
    echo "/usr/local/lib" | sudo tee /etc/ld.so.conf
    sudo ldconfig 
    cd "$SCRIPT_DIR"
fi

#=====================================================
# Get Latest yq
YQ_API_RESPONCE="$(curl -sS -H "Accept: application/vnd.github.v3+json" https://api.github.com/repos/mikefarah/yq/releases/latest)"
YQ_LATEST_URL=""
if ! echo "$YQ_API_RESPONCE" | jq '.message' | grep -q 'rate limit exceeded'; then
    if [[ "$OS_SYSTEM" == "Linux" ]]; then
        YQ_LATEST_URL="$(echo "$YQ_API_RESPONCE" | jq -r '.assets[]|select(.name == "yq_linux_amd64")|.browser_download_url')"
        if [[ -z "$YQ_LATEST_URL" ]]; then
            YQ_LATEST_URL="https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_amd64"
        fi
    elif [[ "$OS_SYSTEM" == "FreeBSD" ]]; then
        YQ_LATEST_URL="$(echo "$YQ_API_RESPONCE" | jq -r '.assets[]|select(.name == "yq_freebsd_amd64")|.browser_download_url')"
        if [[ -z "$YQ_LATEST_URL" ]]; then
            YQ_LATEST_URL="https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_freebsd_amd64"
        fi
    fi
else
    if [[ "$OS_SYSTEM" == "Linux" ]]; then
        YQ_LATEST_URL="https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_amd64"
    elif [[ "$OS_SYSTEM" == "FreeBSD" ]]; then
        YQ_LATEST_URL="https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_freebsd_amd64"
    fi
fi
echo -e "\nYQ Latest URL for ${OS_SYSTEM}: ${YQ_LATEST_URL}\n"

if ! sudo curl -L "$YQ_LATEST_URL" -o /usr/local/bin/yq && sudo chmod +x /usr/local/bin/yq; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Failed to download yq from ${YQ_LATEST_URL}"
    exit 1
fi

#=====================================================
# Install or upgrade RUST
echo
echo '################################################'
echo "---INFO: Install RUST ${RUST_VERSION}"
cd "$HOME"
if ! curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- --default-toolchain ${RUST_VERSION} -y;then
    echo
    echo "###-ERROR(line $LINENO): Can't install RUST. Exit."
    echo
    exit 1
fi
# shellcheck disable=SC1091
source "$HOME/.cargo/env"
cargo install cargo-binutils

######################################################
# Build rust node
    echo
    echo '################################################'
    echo "---INFO: build RUST NODE ..."
    echo -e "${BoldText}${BlueBack}---INFO: NODE git repo:   ${NODE_GIT_REPO} ${NormText}"
    echo -e "${BoldText}${BlueBack}---INFO: NODE git commit: ${NODE_GIT_COMMIT} ${NormText}"
    
    # eval $(ssh-agent -k; ssh-agent -s)
    # If NODE_GIT_REPO url start with git@, set "git-fetch-with-cli = true" in ~/.cargo/config.toml
    if [[ "${NODE_GIT_REPO}" == git@* ]];then
        if [[ ! -f ~/.cargo/config.toml ]];then
            echo -e '[net]\nfetch-with-cli = true' > ~/.cargo/config.toml
        else
            if ! grep -q 'fetch-with-cli = true' ~/.cargo/config.toml;then
                echo -e '\n[net]\nfetch-with-cli = true' >> ~/.cargo/config.toml
            fi
        fi
    fi

    [[ -d "${NODE_SRC_DIR}" ]] && rm -rf "${NODE_SRC_DIR:?}"
    git clone --recurse-submodules  "${NODE_GIT_REPO}" "${NODE_SRC_DIR}"
    cd "${NODE_SRC_DIR}" 
    git checkout "${NODE_GIT_COMMIT}"
    git submodule init && git submodule update --recursive
    git submodule foreach 'git submodule init'
    git submodule foreach 'git submodule update  --recursive'

    cd "${NODE_SRC_DIR}"

    rm -rf ~/.cargo/git/checkouts/ton-*
    rm -rf ~/.cargo/git/checkouts/ever-*

    cargo update

    # Set Link Time Optimization (LTO) for release build
    sed -i.bak '/\[profile\]/,/^$/d' Cargo.toml
    printf '\n[profile.release]\nlto = "fat"\ncodegen-units = 1\npanic = "abort"\n' >> Cargo.toml

    NODE_GIT_NAME="$(yq e '.package.name' "${NODE_SRC_DIR}/Cargo.toml")"
    
    # node git commit
    GIT_COMMIT_EVER_NODE="$(git --git-dir="${NODE_SRC_DIR}/.git" rev-parse HEAD 2>/dev/null)"
    export GIT_COMMIT_EVER_NODE
    # block version
    NODE_BLK_VER=$(grep -A1 'supported_version' "${NODE_SRC_DIR}/src/validating_utils.rs"|tail -1|tr -d ' ')
    export NODE_BLK_VER

    echo -e "${BoldText}${BlueBack}---INFO: NODE build flags: ${NODE_BUILD_FEATURES} commit: ${GIT_COMMIT_EVER_NODE} Block version: ${NODE_BLK_VER}${NormText}"
    RUSTFLAGS="-C target-cpu=native" cargo build --release --features "${NODE_BUILD_FEATURES}"

    # shellcheck disable=SC2086
    find "${NODE_SRC_DIR}/target/release/" -maxdepth 1 -type f ${FEXEC_FLG} -exec sudo cp -f {} "${NODE_BIN_DIR}/" \;
    sudo mv -f  "${NODE_BIN_DIR}/${NODE_GIT_NAME}" "${NODE_BIN_DIR}/${NODE_BIN_NAME}" | cat
    sudo cp -f  "${NODE_BIN_DIR}/${NODE_BIN_NAME}" "${NODE_BIN_DIR}/${NODE_BIN_NAME}-${GIT_COMMIT_EVER_NODE}_${BackUP_Time}" | cat
    # shellcheck disable=SC2012
    ls -1t "${NODE_SRC_DIR}/${NODE_BIN_NAME}-*" | tail -n +5 | xargs rm -f

    echo "---INFO: build RUST NODE ... DONE."

if [[ "$1" == "nodeonly" ]] || [[ "$2" == "nodeonly" ]];then
    rm -f wget-log*
    echo 
    echo '################################################'
    BUILD_END_TIME=$(date +%s)
    Build_mins=$(( (BUILD_END_TIME - BUILD_STRT_TIME)/60 ))
    Build_secs=$(( (BUILD_END_TIME - BUILD_STRT_TIME)%60 ))
    echo
    echo "+++INFO: $(basename "$0") on $VALIDATOR_NAME FINISHED $(date +%s) / $(date)"
    echo "All builds took $Build_mins min $Build_secs secs"
    echo "================================================================================================"
    exit 0
fi

#=====================================================
# Build CLI
echo
echo '################################################'
echo "---INFO: build CLI ... "
echo -e "${BoldText}${BlueBack}---INFO: CLI git repo:   ${CLI_GIT_REPO} ${NormText}"
echo -e "${BoldText}${BlueBack}---INFO: CLI git commit: ${CLI_GIT_COMMIT} ${NormText}"

[[ -d "${CLI_SRC_DIR}" ]] && rm -rf "${CLI_SRC_DIR:?}"
git clone --recurse-submodules "${CLI_GIT_REPO}" "${CLI_SRC_DIR}"
cd "${CLI_SRC_DIR}"
git checkout "${CLI_GIT_COMMIT}"
cargo update
RUSTFLAGS="-C target-cpu=native" cargo build --release
sudo cp "${CLI_SRC_DIR}/target/release/ever-cli" "$NODE_BIN_DIR/$CLI_BIN_NAME"
echo "---INFO: build ever-cli ... DONE"

echo 
echo '################################################'
BUILD_END_TIME=$(date +%s)
Build_mins=$(( (BUILD_END_TIME - BUILD_STRT_TIME)/60 ))
Build_secs=$(( (BUILD_END_TIME - BUILD_STRT_TIME)%60 ))
echo
echo "+++INFO: $(basename "$0") on $VALIDATOR_NAME FINISHED $(date +%s) / $(date)"
echo "All builds took $Build_mins min $Build_secs secs"
echo "================================================================================================"

exit 0
