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

echo "################################### Docker setup script ########################################"
SelfScriptName=$(basename "$0")
echo "--- INFO: ${SelfScriptName} BEGIN $(date +%s) / $(date  +'%F %T %Z')"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
if ! source "${SCRIPT_DIR}/../env.sh"; then
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

if ! $IS_ENVIRONMENT_CONFIGURED || [[ -z "${NODE_IP_ADDR}" ]]; then
    echo "###-ERROR(${SelfScriptName} line $LINENO): Node IP address is not set in env.sh"
    exit 1
fi

#=====================================================
# Check utilities is installed
if ! command -v yq &>/dev/null; then echo "###-ERROR(${SelfScriptName} line $LINENO): 'yq' is not installed. Please install it and run the script again."; exit 1; fi
if ! command -v jq &>/dev/null; then echo "###-ERROR(${SelfScriptName} line $LINENO): 'jq' is not installed. Please install it and run the script again."; exit 1; fi
if ! command -v bc &>/dev/null; then echo "###-ERROR(${SelfScriptName} line $LINENO): 'bc' is not installed. Please install it and run the script again."; exit 1; fi
# Check docker related utilities
if ! command -v docker &>/dev/null; then echo "###-ERROR(${SelfScriptName} line $LINENO): 'docker' is not installed. Please install it and run the script again."; exit 1; fi
if ! command -v docker-compose &>/dev/null; then echo "###-ERROR(${SelfScriptName} line $LINENO): 'docker-compose' is not installed. Please install it and run the script again."; exit 1; fi
if ! docker buildx &>/dev/null; then echo "###-ERROR(${SelfScriptName} line $LINENO): 'docker buildx' is not installed. Please install it and run the script again."; exit 1; fi

#=====================================================
# Check and start docker network if not exists
Proxy_Net_Name="proxy_nw"
echo -e "\n---INFO: Checking docker network '${Proxy_Net_Name}'"

# Check if docker network exists
if docker network ls | grep -qw "${Proxy_Net_Name}"; then
    echo "---INFO: Docker network '${Proxy_Net_Name}' already exists. Skipping creation."
else
    echo "---INFO: Starting docker network '${Proxy_Net_Name}'"
    if ! docker network create "${Proxy_Net_Name}"; then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Can't create docker network '${Proxy_Net_Name}'"
        exit 1
    fi
fi

#=====================================================
# Set and start statsd container
echo -e "\n---INFO: Setting up and start statsd container"
sed -i \
    -e "s|^External_IP=.*|External_IP=${STATSD_EXTERNAL_IP}|" \
    -e "s|^UDP_PORT=.*|UDP_PORT=${STATSD_UDP_PORT}|" \
    -e "s|^TCP_PORT=.*|TCP_PORT=${STATSD_TCP_PORT}|" \
    "${DOCKER_STATSD_ENV_FILE}"

# check if statsd container is running
if ! docker ps -a --format '{{.Names}}' | grep -qw "${DOCKER_STATSD_CONTAINER_NAME}"; then
    pushd "${DOCKER_STATSD_DIR}" || { echo "###-ERROR(${SelfScriptName} line $LINENO): Can't change the directory to ${DOCKER_STATSD_DIR}"; exit 1; }
    if ! docker-compose up -d; then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Can't start statsd container"
        exit 1
    fi
    popd || { echo "###-ERROR(${SelfScriptName} line $LINENO): Can't change the directory to previous"; exit 1; }
fi
#=====================================================
# Set and build node docker image
if ${DOCKER_USE_CUSTOM_IMAGE}; then
    # Build docker image if DOCKER_USE_CUSTOM_IMAGE=true
    echo -e "\n---INFO: Building custom docker image"
    cd "${DOCKER_NODE_BUILD_DIR}" || { echo "###-ERROR(${SelfScriptName} line $LINENO): Can't change directory to ${NODE_TOP_DIR}/docker-compose/ever-node/build"; exit 1; }
    # Copy sources from top sources to build directory
    mkdir -p "${DOCKER_NODE_BUILD_DIR}/src" || { echo "###-ERROR(${SelfScriptName}: line $LINENO) Can't create directory ${DOCKER_NODE_BUILD_DIR}"; exit 1; }
    cp -r "${NODE_SRC_DIR}" "${DOCKER_NODE_BUILD_DIR}/src/" || { echo "###-ERROR(${SelfScriptName} line $LINENO): Can't copy sources to ${DOCKER_NODE_BUILD_DIR}"; exit 1; }
    cp -r "${CLI_SRC_DIR}" "${DOCKER_NODE_BUILD_DIR}/src/" || { echo "###-ERROR(${SelfScriptName} line $LINENO): Can't copy scripts to ${DOCKER_NODE_BUILD_DIR}"; exit 1; }
    Node_Repo_Dir_Name=$(basename "${NODE_SRC_DIR}")
    Cli_Repo_Dir_Name=$(basename "${CLI_SRC_DIR}")

    Docker_Node_Sources="${DOCKER_NODE_BUILD_DIR}/src/${Node_Repo_Dir_Name}"
    Docker_Cli_Sources="${DOCKER_NODE_BUILD_DIR}/src/${Cli_Repo_Dir_Name}"
    
    NODE_VERSION="$(yq e '.package.version' "${Docker_Node_Sources}/Cargo.toml")"
    NODE_GIT_NAME="$(yq e '.package.name' "${Docker_Node_Sources}/Cargo.toml")"
    CLI_GIT_NAME="$(yq e '.package.name' "${Docker_Cli_Sources}/Cargo.toml")"
    WORK_DOCKER_IMAGE="${DOCKER_IMAGE_REPO}:${NODE_VERSION}"

    if ! docker buildx build --no-cache "${DOCKER_NODE_BUILD_DIR}" \
        --build-arg RUST_VERSION=$RUST_VERSION \
        --build-arg YQ_VERSION=$YQ_VERSION \
        --build-arg NODE_SRC_DIR=$Node_Repo_Dir_Name \
        --build-arg NODE_BUILD_FEATURES=$NODE_BUILD_FEATURES \
        --build-arg NODE_GIT_NAME=$NODE_GIT_NAME \
        --build-arg NODE_BIN_NAME=$NODE_BIN_NAME \
        --build-arg CLI_SRC_DIR=$Cli_Repo_Dir_Name \
        --build-arg CLI_GIT_NAME=$CLI_GIT_NAME \
        --build-arg CLI_BIN_NAME=$CLI_BIN_NAME \
        --tag "${WORK_DOCKER_IMAGE}" --platform linux/amd64
    then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Can't build docker image"
        exit 1
    else
        echo "---INFO: Docker image ${WORK_DOCKER_IMAGE} has been built"
        container_id=$(docker run -d --entrypoint /bin/sh $WORK_DOCKER_IMAGE -c "tail -f /dev/null")
        sleep 5
        if [[ -z "${container_id}" ]]; then
            echo "###-ERROR(${SelfScriptName} line $LINENO): Can't run docker container"
            exit 1
        else    
            echo "---INFO: Docker container ${container_id} has been started"
        fi
        echo "---INFO: Cleaning up docker images"
        docker image prune -af
        docker rm -f $container_id
    fi
else
    #=====================================================
    # Get actual docker image version from latest release
    if [[ "${DOCKER_IMAGE_TAG}" == "latest" ]]; then
        # Get auth token
        TOKEN=$(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${DOCKERHUB_USER}/${DOCKERHUB_REPO}:pull" | jq -r .token)
        # Get the image digest for the 'latest' tag
        DIGEST=$(curl -s -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.docker.distribution.manifest.v2+json" "https://registry-1.docker.io/v2/${DOCKERHUB_USER}/${DOCKERHUB_REPO}/manifests/latest" | jq -r '.config.digest')
        # Get tags 
        TAGS=$(curl -s -H "Authorization: Bearer $TOKEN" "https://registry-1.docker.io/v2/${DOCKERHUB_USER}/${DOCKERHUB_REPO}/tags/list" | jq -r ".tags[]")
        # Look for the tag that corresponds to the digest
        for tag in $TAGS; do
            TAG_DIGEST=$(curl -s -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.docker.distribution.manifest.v2+json" "https://registry-1.docker.io/v2/${DOCKERHUB_USER}/${DOCKERHUB_REPO}/manifests/$tag" | jq -r '.config.digest')
            if [ "$DIGEST" == "$TAG_DIGEST" ]; then
                VERSION_LATEST_TAG="${tag}"
                break
            fi
        done
    else
        VERSION_LATEST_TAG="${DOCKER_IMAGE_TAG}"
    fi
    if [[ -z "${VERSION_LATEST_TAG}" ]]; then
        echo "###-ERROR(${SelfScriptName} line $LINENO): Can't get docker image version"
        exit 1
    fi
    WORK_DOCKER_IMAGE="${DOCKER_IMAGE_REPO}:${VERSION_LATEST_TAG}"

    echo -e "\n---INFO: Using pre-built docker image: ${WORK_DOCKER_IMAGE}"
    if ! docker pull ${WORK_DOCKER_IMAGE}; then 
        echo "###-ERROR(${SelfScriptName} line $LINENO): Can't pull docker image ${WORK_DOCKER_IMAGE}"
        exit 1
    fi
    
    # Check if the image has node binary in /usr/local/bin
    if ! docker run --rm --entrypoint /bin/ls ${WORK_DOCKER_IMAGE} /usr/local/bin/ever-node >/dev/null 2>&1
    then
        # Check if the image has node binary in /ton-node
        if docker run --rm --entrypoint /bin/ls ${WORK_DOCKER_IMAGE} /ton-node/ton_node_kafka >/dev/null 2>&1
        then
            echo "---INFO: Found node binary in /ton-node/ton_node_kafka"
            echo "---INFO: Setting up link to /usr/local/bin/ever-node"
            # Get the original image id
            OLD_IMAGE_ID=$(docker images -q ${WORK_DOCKER_IMAGE})
            echo "---INFO: Original image id: ${OLD_IMAGE_ID}"
            # Set up link to /usr/local/bin/ever-node in the docker image
            container_id=$(docker run -d --entrypoint /bin/sh $WORK_DOCKER_IMAGE -c "tail -f /dev/null")
            sleep 5
            if [[ -z "${container_id}" ]]; then
                echo "###-ERROR(${SelfScriptName}: line $LINENO): Can't run docker container"
                exit 1
            else
                echo "---INFO: Docker container ${container_id} has been started"
            fi
            docker exec $container_id /bin/sh -c "find /ton-node -type f -exec ln -fns {} /usr/local/bin/ \;"
            docker exec $container_id /bin/sh -c "find /ton-node/tools -type f -exec ln -fns {} /usr/local/bin/ \;"
            docker exec $container_id ln -fns /ton-node/ton_node_no_kafka /usr/local/bin/ever-node
            docker exec $container_id ln -fns /ton-node/ton_node_kafka /usr/local/bin/ever-node_kafka
            docker exec $container_id ln -fns /ton-node/tools/tonos-cli /usr/local/bin/ever-cli
            docker exec $container_id ls -l /usr/local/bin/
            docker exec $container_id /bin/sh -c "echo >> /etc/profile; echo 'umask 000' >> /etc/profile"
            docker exec $container_id /bin/sh -c "echo >> /etc/bash.bashrc; echo 'umask 000' >> /etc/bash.bashrc"
            docker commit $container_id $WORK_DOCKER_IMAGE
            NEW_IMAGE_ID=$(docker images -q ${WORK_DOCKER_IMAGE})
            echo "---INFO: New image id: ${NEW_IMAGE_ID}"
            docker rm -f $container_id
            if [[ "${OLD_IMAGE_ID}" == "${NEW_IMAGE_ID}" ]]; then
                echo "###-ERROR(${SelfScriptName} line $LINENO): Can't commit changes to the docker image"
                exit 1
            else
                echo "---INFO: Link to /usr/local/bin/ever-node has been set"
            fi
        else
            echo "###-ERROR(${SelfScriptName} line $LINENO): Can't find node binary in the docker image"
            exit 1
        fi
    fi
fi

#=====================================================
# Set up node docker environment
echo -e "\n---INFO: Setting up node docker environment in $DOCKER_NODE_ENV_FILE file"
# Set docker memory limit
HOST_MEM_TOTAL_BYTES=$(grep MemTotal /proc/meminfo | awk '{print $2}')
MEM_LIMIT=$(echo "${HOST_MEM_TOTAL_BYTES} / 1024 / 1024 - 1" | $CALL_BC | awk '{print int($1)}')
echo "---INFO: Setting up docker memory limit to ${MEM_LIMIT} GB"
# Set docker image version
echo "---INFO: Setting up docker image version to ${WORK_DOCKER_IMAGE}"
sed -i \
    -e "s|^RUST_VERSION=.*|RUST_VERSION=${RUST_VERSION}|" \
    -e "s|^YQ_VERSION=.*|YQ_VERSION=${YQ_VERSION}|" \
    -e "s|^MEM_LIMIT=.*|MEM_LIMIT=${MEM_LIMIT}G|" \
    -e "s|^WORK_DOCKER_IMAGE=.*|WORK_DOCKER_IMAGE=${WORK_DOCKER_IMAGE}|" \
    -e "s|^CONTAINER_NAME=.*|CONTAINER_NAME=${DOCKER_NODE_CONTAINER_NAME}|" \
    -e "s|^CONTAINER_RUN_MODE=.*|CONTAINER_RUN_MODE=${NODE_ROLE}|" \
    -e "s|^STATSD_DOMAIN=.*|STATSD_DOMAIN=${STATSD_DOMAIN}|" \
    -e "s|^STATSD_PORT=.*|STATSD_PORT=${STATSD_UDP_PORT}|" \
    -e "s|^ADNL_PORT=.*|ADNL_PORT=${ADNL_PORT}|" \
    -e "s|^VALIDATOR_NAME=.*|VALIDATOR_NAME=${VALIDATOR_NAME}|" \
    "${DOCKER_NODE_ENV_FILE}"


#=====================================================================================================
# Cleanup old docker images
# Get a list of all images with a tag containing 'ever-node', sorted by the number in the tag
images=$(docker image ls --format '{{.Tag}} {{.ID}}' | grep '\-node-' | sort -t'-' -k4 -n)
# Get the count of images
image_count=$(echo "$images" | wc -l)
# Save the last four images
images_to_keep=$(echo "$images" | tail -4)
# Remove the remaining images
for image in $(echo "$images" | head -n $((image_count - 4)) | awk '{print $2}'); do
    echo "Removing image: $image"
    docker image rm "$image"
done
# Print the remaining images
echo "Remaining images:"
echo "$images_to_keep"
exit 0
