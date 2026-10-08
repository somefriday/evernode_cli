# evernode operator runbook

This runbook installs the CLI once and manages all validator state below
`/var/lib/ever-validator`. Every command below can be run from any directory.
Run it as an Ubuntu or Debian root-capable operator. The CLI creates its own node-specific
database, keys, Docker containers, StatsD exporter, ports and election cron
file.

The commands use `validator01` as an example name. Replace it with a stable,
unique validator name for each node.

## 0. Host prerequisites

Run these commands on a supported Ubuntu or Debian server before installing the CLI:

```bash
sudo apt-get update
sudo apt-get install -y \
  ca-certificates \
  curl \
  git \
  jq \
  bc \
  gawk \
  util-linux \
  procps \
  python3 \
  python3-pip \
  python3-venv \
  tmux
```

`tmux` is optional for the node itself, but recommended for long image builds
and synchronization sessions. A host Rust installation is intentionally not
required: the maintained Dockerfile installs the pinned Rust toolchain inside
the builder image. This keeps host Rust and Cargo versions out of the build.

## 1. Install the CLI

Install the command globally from the cloned checkout. The CLI is isolated in
a virtual environment, so it does not modify the operating system's managed Python:

```bash
sudo install -d -m 0755 /opt/evernode
sudo python3 -m venv /opt/evernode/venv
sudo /opt/evernode/venv/bin/python -m pip install --upgrade pip
sudo /opt/evernode/venv/bin/python -m pip install /root/node-cli-tool
sudo ln -sfn /opt/evernode/venv/bin/evernode /usr/local/bin/evernode

evernode --version
```

## 2. Install Docker Engine, Buildx and Compose

Install Docker **before building an image**:

```bash
sudo evernode host setup --yes
sudo docker version
sudo evernode host check
```

`docker version` must show both a **Client** and a **Server** section before
continuing. `host setup` detects Ubuntu or Debian, uses that distribution's official Docker repository, and installs
Docker Engine, Buildx and the Docker Compose plugin. It also installs `cron`,
`chrony`, the required `yq` v4 binary, and starts Docker, cron and chrony. It
does not open firewall ports. When a node is created, allow only its allocated
**ADNL UDP** port through the provider and host firewall. Metrics bind to
`127.0.0.1`; console and StatsD ports remain inside Docker.

To update the installed CLI after replacing `/root/node-cli-tool`, run:

```bash
sudo /opt/evernode/venv/bin/python -m pip install --upgrade /root/node-cli-tool
```

## 3. Build a shared base image

Build the Everscale node image once. The default sources are
`everx-labs/ever-node` at `master` and `everx-labs/ever-cli` at `0.44.0`; the
CLI records the resolved commits, Rust version and Dockerfile fingerprint.
It also initializes required Git submodules from the selected ever-node
revision before Docker receives the source tree.

```bash
sudo evernode image build --yes
sudo evernode image list
```

Copy the `image` value reported by `image list`, for example
`local/ever-node:0123456789abcdef`. Call it `IMAGE` in the commands below.
The same immutable image can be attached to multiple nodes. Node databases,
keys and containers are still isolated.

To build a particular ever-node revision, specify it explicitly:

```bash
sudo evernode image build \
  --node-repo https://github.com/everx-labs/ever-node.git \
  --node-ref TAG_OR_COMMIT \
  --cli-repo https://github.com/everx-labs/ever-cli.git \
  --cli-ref 0.44.0 \
  --yes
```

## 4. Fresh validator: node, wallet, DePool, elections and validation

Before beginning, make sure the server has enough disk, RAM and a public IPv4.
Fund the multisig wallet and DePool when the CLI asks you to do so. Keep a
secure backup of the generated key material; it is stored root-only under
`/var/lib/ever-validator/nodes/validator01/keys`.

Create the node from the image built above:

```bash
sudo evernode node create -n validator01 --image IMAGE
```

The fresh-DePool flow uses these defaults when Enter is pressed:

| Setting | Default |
| --- | --- |
| Network / workchain | `main` / `0` |
| Election check interval | `10` minutes |
| DePool type | `EverX` |
| Validator assurance | `50000` EVER |
| Minimum stake | `10` EVER |
| Participant reward fraction | `65`% |
| DePool balance threshold | `20` EVER |
| Multisig custodians / required signatures | `3` / `2` |

The tool discovers a public IPv4 as a proposed value, but confirm that it is
the address actually forwarded to this server. It also allocates the first free
container pair (`ever-node-01`, `statsd-01`, then `02`, and so on), an unused
ADNL UDP port starting at `58888`, and a loopback metrics port starting at
`9102`. Provide a measured memory limit when prompted; it is a Docker ceiling,
not a reservation.

Wait until the node is fully synchronized. This command continues to poll
until it sees two consecutive synchronized responses:

```bash
sudo evernode node sync -n validator01 --wait
sudo evernode node status -n validator01
```

Complete the wallet and DePool stages in order. Each command performs one
named action and prints its exact next command.

```bash
# 1. Generate the Safe wallet. Save every displayed seed phrase and address.
sudo evernode wallet create -n validator01

# 2. Fund the displayed Safe wallet, then deploy it.
sudo evernode wallet deploy -n validator01

# 3. Generate the DePool identity. Save its displayed seed phrase and address.
sudo evernode depool prepare -n validator01

# 4. Fund the displayed DePool, then deploy it.
sudo evernode depool deploy -n validator01

# 5. Make the configured initial stake, then install elections.
sudo evernode depool stake-initial -n validator01
sudo evernode election start -n validator01
sudo evernode election status -n validator01
```

`wallet create` and `depool prepare` require an interactive terminal. They
print new seed phrases directly to that terminal and require you to acknowledge
that they were saved. Phrases are not included in logs or JSON output.

The election schedule is an isolated root-owned file at
`/etc/cron.d/evernode-validator01`. On every interval it runs the reference
Ever-Validator sequence:

```text
prepare_elections.sh → take_part_in_elections.sh
```

Check live validation and logs with:

```bash
sudo evernode node status -n validator01
sudo evernode node logs -n validator01 --component node --follow
sudo evernode node logs -n validator01 --component stderr --tail 100
sudo evernode node resources -n validator01
sudo evernode election status -n validator01
```

## 5. Import an existing validator wallet and DePool

This flow builds a new node instance from an existing managed image and
recovers its Safe wallet using seed phrases. It does **not** restore the former
node's ADNL, validator or console identity. Do not enable elections until the
former validator instance is fenced and the intended node identity is in place.

You need the wallet address, DePool address, the number of custodians and the
required signature threshold. The CLI reads each seed phrase through a hidden
terminal prompt. It never accepts phrases as command arguments, environment
variables or standard input.

```bash
sudo evernode node create -n validator02 \
  --image IMAGE \
  --import-wallet \
  --wallet-address 0:YOUR_64_HEX_WALLET_ADDRESS \
  --depool-address 0:YOUR_64_HEX_DEPOOL_ADDRESS \
  --custodians 3 \
  --required-signatures 2
```

Confirm the network settings and public IPv4, then enter one phrase for each
custodian when prompted. The phrases are written as root-only `0600` files in
the node's key directory.

An imported DePool keeps its existing on-chain assurance, minimum stake,
reward fraction and balance threshold. The CLI does not prompt for those
new-DePool deployment parameters during import. Select the existing DePool
type correctly (`EverX` or `StEver`), because its ABI is needed to verify and
operate that contract.

Synchronize and verify the recovered wallet against chain state:

```bash
sudo evernode node sync -n validator02 --wait
sudo evernode wallet recover -n validator02
sudo evernode wallet verify -n validator02
sudo evernode depool verify -n validator02
sudo evernode election start -n validator02
sudo evernode election status -n validator02
```

`wallet recover` derives the wallet address from stored phrases. `wallet
verify` checks its custodian count and required-signature threshold. `depool
verify` checks the supplied DePool through the reference helper. These commands
do not deploy contracts or make a stake.

## 6. Update one validator to a rebuilt image

Do planned image maintenance outside of the node's current and next validator
sets. The updater refuses to switch an image while the node reports membership
in either set. It preserves the database, node configuration, wallet and keys.

1. Check the node and stop its owned election schedule. This prevents new
   election applications; it does not withdraw existing stake.

   ```bash
   sudo evernode node status -n validator01
   sudo evernode election stop -n validator01
   sudo evernode election status -n validator01
   ```

2. Wait until `node status` shows that the node is outside its current and next
   validator sets. Then rebuild the image currently attached to it. Obtain the
   old image from the `image` field in the status/config output.

   ```bash
   sudo evernode node config -n validator01
   sudo evernode image rebuild OLD_IMAGE --yes
   sudo evernode image list
   ```

   Copy the new `image` value from `image list` as `NEW_IMAGE`. A rebuild always
   produces a new immutable image record, even if the repository resolves to
   the same commit.

3. Attach the new image. The CLI stops the node and its StatsD exporter,
   rewrites only this node's Compose definition, restarts it, and requires two
   consecutive synchronized checks before reporting success.

   ```bash
   sudo evernode node update -n validator01 --image NEW_IMAGE --yes
   sudo evernode node status -n validator01
   ```

4. When the node is synchronized and you are ready to resume participation,
   reinstall its election schedule:

   ```bash
   sudo evernode election start -n validator01
   sudo evernode election status -n validator01
   ```

If the replacement does not become ready, inspect logs and explicitly return
to the saved image:

```bash
sudo evernode node logs -n validator01 --component stderr --tail 100
sudo evernode node update -n validator01 --rollback --yes
```

## 7. Seed a new local node from another node's database

Use this only to speed up an **unsynchronized** target on the same server. The
source must be fully synchronized, have no active election schedule, and
explicitly report that it is outside both validator sets. The target must also
have no active election schedule. The command stops both containers briefly,
copies only block/database data, excludes `catchains/`, then restarts the
source before starting the target. It never copies keys, seed phrases, node
configuration, or wallet data.

Review the checks without changing either node:

```bash
sudo evernode node lsync --from validator01 --to validator02 --dry-run
```

Run the copy after reviewing the plan:

```bash
sudo evernode node lsync --from validator01 --to validator02
sudo evernode node sync -n validator02 --wait
```

`lsync` refuses a running election schedule, a source in a current or next
validator set, different image IDs, different global-network configurations,
insufficient staging space, or a target already synchronized. If the copied
database does not start, it restores the target's previous database. Do not
use it to clone a validating source node.

## Routine commands

```bash
sudo evernode node list
sudo evernode image list
sudo evernode election list
sudo evernode doctor -n validator01

# Stop one node with a 30-second graceful shutdown. Its owned election schedule
# is disabled automatically.
sudo evernode node stop -n validator01 --timeout 30

sudo evernode node start -n validator01

# Stop every managed node and every managed election schedule.
sudo evernode election stop --all
sudo evernode node stop --all --timeout 30
```

Do not remove an image while any node uses it. `evernode image remove IMAGE`
checks managed nodes and Docker containers before deleting a record.

## Monitoring

Replace `validator02` with the managed node name you want to inspect.

Check container health, console statistics, synchronization state, current
block, time difference and restarts:

```bash
sudo evernode node status -n validator02
```

Read the most recent node log entries. Use this for normal synchronization
progress, peer activity, warnings and errors:

```bash
sudo evernode node logs -n validator02 --component node --tail 200
```

Read only the node process's standard-error log. It is normally empty; output
here usually identifies a startup failure, panic, or configuration problem:

```bash
sudo evernode node logs -n validator02 --component stderr --tail 100
```

Show current CPU, memory, network and disk I/O for both the node and its
StatsD exporter, including the node memory limit and OOM state:

```bash
sudo evernode node resources -n validator02
```

During initial sync, follow only persistent-state download progress. Increasing
`got part offset` values mean the node is receiving the snapshot; the reported
masterchain block can remain unchanged until loading completes:

```bash
sudo evernode node logs -n validator02 --component node --follow |
  grep --line-buffered 'download_persistent_state'
```
