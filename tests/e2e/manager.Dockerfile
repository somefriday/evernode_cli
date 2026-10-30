# A bare Ubuntu server, provisioned exactly as README.md tells an operator to.
#
# Build context must hold the checkout at project/:
#   docker build -t evernode-prod:ubuntu24 -f tests/e2e/manager.Dockerfile <context>
#
# This image is a Linux host stand-in. It installs only what README.md's
# install section installs; `evernode host setup` provides the rest, so that
# the documented provisioning path is what the run exercises.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y python3 python3-venv git && rm -rf /var/lib/apt/lists/*
COPY project/ /root/evernode_cli/
RUN install -d -m 0755 /opt/evernode \
 && python3 -m venv /opt/evernode/venv \
 && /opt/evernode/venv/bin/python -m pip install --upgrade pip \
 && /opt/evernode/venv/bin/python -m pip install /root/evernode_cli \
 && ln -sfn /opt/evernode/venv/bin/evernode /usr/local/bin/evernode
WORKDIR /root
