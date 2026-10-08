# Maintained build recipe for evernode-manager.  Sources are supplied by the
# image builder as src/ever-node and src/ever-cli; this recipe never edits them.
FROM ubuntu:22.04 AS builder

ARG RUST_VERSION=1.90.0
ARG NODE_BUILD_FEATURES=statsd
ARG NODE_SRC_DIR=ever-node
ARG CLI_SRC_DIR=ever-cli
ARG NODE_BIN_NAME=ever-node
ARG CLI_BIN_NAME=ever-cli
ARG YQ_VERSION=4.44.5

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/root/.cargo/bin:${PATH} \
    RUSTUP_TOOLCHAIN=${RUST_VERSION} \
    ZSTD_LIB_DIR=/usr/lib/x86_64-linux-gnu \
    CARGO_PROFILE_RELEASE_LTO=fat \
    CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1 \
    CARGO_PROFILE_RELEASE_PANIC=abort

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git build-essential cmake gperf pkg-config \
    clang libclang-dev llvm-dev libssl-dev openssl zlib1g-dev libzstd-dev \
    librdkafka-dev libreadline-dev libgoogle-perftools-dev \
    jq bc util-linux procps && \
    rm -rf /var/lib/apt/lists/* && \
    curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs | sh -s -- -y --default-toolchain "${RUST_VERSION}" && \
    curl -fL "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_amd64" -o /usr/local/bin/yq && \
    chmod 0755 /usr/local/bin/yq

COPY src/${NODE_SRC_DIR} /build/${NODE_SRC_DIR}
COPY src/${CLI_SRC_DIR} /build/${CLI_SRC_DIR}

RUN mkdir -p /out && \
    cd /build/${NODE_SRC_DIR} && \
    rustup run "${RUST_VERSION}" cargo update && \
    rustup run "${RUST_VERSION}" cargo build --release --features "${NODE_BUILD_FEATURES}" && \
    find target/release -maxdepth 1 -type f -executable -exec cp -f {} /out/ \; && \
    rustup run "${RUST_VERSION}" cargo build --release --features "external_db,${NODE_BUILD_FEATURES}" && \
    cp "target/release/${NODE_BIN_NAME}" "/out/${NODE_BIN_NAME}_kafka" && \
    cd /build/${CLI_SRC_DIR} && \
    rustup run "${RUST_VERSION}" cargo update && \
    rustup run "${RUST_VERSION}" cargo build --release && \
    cp "target/release/${CLI_BIN_NAME}" "/out/${CLI_BIN_NAME}"

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    ZSTD_LIB_DIR=/usr/lib/x86_64-linux-gnu

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates bash curl git jq bc util-linux procps \
    openssl zlib1g libzstd1 libssl3 librdkafka1 libreadline8 gawk \
    libgoogle-perftools4 && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /ever-node/bin

COPY --from=builder /usr/local/bin/yq /usr/local/bin/yq
COPY --from=builder /out/ /ever-node/bin/
RUN ln -s /ever-node/bin/* /usr/local/bin/
