# evernode

`evernode` builds and operates isolated Everscale validator nodes in Docker on
Ubuntu and Debian. It wraps the established Ever-Validator wallet, DePool,
stake, and election scripts while keeping each node's database, keys,
containers, metrics, ports, and cron schedule separate.

Matching source inputs share one immutable node image. Node data is stored
root-only under `/var/lib/ever-validator`.

## Install

Clone this repository to the server, then install the command in its own
virtual environment:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv git
git clone REPOSITORY_URL /root/node-cli-tool

sudo install -d -m 0755 /opt/evernode
sudo python3 -m venv /opt/evernode/venv
sudo /opt/evernode/venv/bin/python -m pip install --upgrade pip
sudo /opt/evernode/venv/bin/python -m pip install /root/node-cli-tool
sudo ln -sfn /opt/evernode/venv/bin/evernode /usr/local/bin/evernode

sudo evernode host setup --yes
sudo evernode host check
```

`host setup` detects Ubuntu or Debian, installs Docker Engine, Buildx, Compose,
and the reference-script dependencies. It does not modify the firewall. Allow
only each node's allocated ADNL UDP port through the host and provider firewall.

## Build an image

Build the shared image once, then copy its `image` value.

```bash
sudo evernode image build --yes
sudo evernode image list
```

Use that value as `IMAGE` below, for example
`local/ever-node:0123456789abcdef`.

## New validator

Create and synchronize the node:

```bash
sudo evernode node create -n validator01 --image IMAGE
sudo evernode node sync -n validator01 --wait
```

Complete each on-chain stage in order. Save every phrase and address displayed
by `wallet create` and `depool prepare`, then fund the requested contract
before its deployment command.

```bash
sudo evernode wallet create -n validator01
sudo evernode wallet deploy -n validator01
sudo evernode depool prepare -n validator01
sudo evernode depool deploy -n validator01
sudo evernode depool stake-initial -n validator01
sudo evernode election start -n validator01
```

## Import an existing Safe wallet and DePool

Create a new node from the shared image and provide the existing on-chain
addresses. Phrases are requested through hidden terminal prompts and saved as
root-only files.

```bash
sudo evernode node create -n validator02 \
  --image IMAGE \
  --import-wallet \
  --wallet-address 0:YOUR_64_HEX_WALLET_ADDRESS \
  --depool-address 0:YOUR_64_HEX_DEPOOL_ADDRESS \
  --custodians 3 \
  --required-signatures 2

sudo evernode node sync -n validator02 --wait
sudo evernode wallet recover -n validator02
sudo evernode wallet verify -n validator02
sudo evernode depool verify -n validator02
sudo evernode election start -n validator02
```

Wallet phrases restore the Safe wallet only. They do not restore the former
node's ADNL, consensus, or console keys. Fence the former validator before
starting elections on a replacement.

## Operate a node

```bash
sudo evernode node list
sudo evernode node status -n validator01
sudo evernode node logs -n validator01 --component node --follow
sudo evernode node resources -n validator01
sudo evernode election status -n validator01
sudo evernode election stop -n validator01
sudo evernode node stop -n validator01 --timeout 30
sudo evernode node start -n validator01
```

For image maintenance, local database seeding, rollback, monitoring, and the
complete setup flow, read the [operator runbook](instruction.md). The
[command reference](documentation.md) describes each workflow, and
[focused_delivery.md](focused_delivery.md) maps CLI actions to the vendored
Ever-Validator scripts.

## Development

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
