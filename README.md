# evernode

Runs Everscale validator nodes in Docker on Ubuntu and Debian. Several nodes
per server, each with its own database, keys, containers, ports and election
schedule. Node data lives under `/var/lib/ever-validator`.

Run as root. Full guide: [instruction.md](instruction.md). Every command, with its
flags: [docs/](docs/README.md).

## Install

```bash
apt-get update
apt-get install -y python3 python3-venv git
git clone https://github.com/somefriday/evernode_cli.git /root/evernode_cli   # for updates; the election scripts install with evernode

install -d -m 0755 /opt/evernode
python3 -m venv /opt/evernode/venv
/opt/evernode/venv/bin/python -m pip install --upgrade pip
/opt/evernode/venv/bin/python -m pip install /root/evernode_cli
ln -sfn /opt/evernode/venv/bin/evernode /usr/local/bin/evernode

evernode host setup --yes   # Docker and script dependencies
evernode host check
```

Open each node's ADNL UDP port yourself; `host setup` does not touch the firewall.

## Update

```bash
cd /root/evernode_cli && git pull --ff-only
/opt/evernode/venv/bin/python -m pip install --upgrade /root/evernode_cli
evernode node sync-scripts --all   # the install alone does not refresh these
```

A node created before evernode shipped its own election scripts still takes them
from the old checkout path or a clone of the upstream kit. Move it once with
`evernode node sync-scripts -n NAME --bundled`; the plan shows the move first.

## New validator

```bash
evernode node create -n validator01        # menu: type, network, image
evernode node sync -n validator01 --wait
evernode wallet create -n validator01      # prints seed phrases
evernode wallet deploy -n validator01      # fund the address first
evernode depool prepare -n validator01     # prints seed phrases
evernode depool deploy -n validator01      # fund the address first
evernode depool stake-initial -n validator01
evernode election start -n validator01
```

Once the first stake has entered an election, add the second: `evernode depool stake -n validator01 --amount 50000`. With two rounds funded, the DePool validates every round instead of every other one.

**Save every seed phrase offline before funding anything.** The only other copy
is the node's root-only `keys/` directory.

Validator types, chosen in the `node create` menu:

- `depool` — stake in a shared pool, node bids for the pool
- `solo` — node stakes its own money, no pool
- `nominator` — cold owner holds the stake, server may bid and recover only

The commands above are the DePool sequence; the solo and nominator sequences are
in [docs/README.md](docs/README.md). `node status` prints the next step whenever
you lose your place.

## Everyday

```bash
evernode node list                        # every node and its state
evernode node status -n validator01       # health, sync, next step
evernode node logs -n validator01 --follow
evernode doctor -n validator01            # status, free disk, schedule file
evernode depool info -n validator01       # rounds, stake, pending transactions
evernode election status -n validator01   # schedule and validator set
evernode node restart -n validator01      # after election stop; refuses while in a validator set
```

Groups: `host`, `node`, `wallet`, `depool`, `stake`, `nominator`, `election`,
`image`, `doctor`. Add `--help` to any of them.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
