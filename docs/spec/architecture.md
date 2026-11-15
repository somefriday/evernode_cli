# Architecture

Which parts exist, where each one runs, and how they reach the chain and each other.

## What the system does

evernode runs Everscale validator nodes in Docker on one Ubuntu or Debian server and does a
validator's work for each node. It builds the node image, creates the node, sets up its wallet and
staking contracts, bids in every election from cron, and votes on network config changes. One
server holds several nodes. Each node has its own directory, containers, ports, keys and schedule.

A node stakes in one of three ways, its *validator type*:

| Type        | Who holds the stake                                   | Who sends the bid to the elector               |
| ----------- | ----------------------------------------------------- | ---------------------------------------------- |
| `depool`    | a DePool contract, shared with its participants       | the DePool, through a proxy, when the Safe asks |
| `solo`      | the node's own Safe wallet                            | the Safe                                       |
| `nominator` | a single-nominator contract that a cold wallet owns   | the contract, when the Safe asks               |

In every type the node's Safe signs on the server. Only in the nominator type is the key that can
withdraw the stake kept off the server.

## The parts

```
operator
   │
evernode (Python, root, on the host)
   ├── docker, docker compose ──── node container: ever-node, console, ever-cli
   │                               exporter container: statsd metrics
   ├── kit scripts (bash, on the host) ── docker exec ── console, ever-cli
   ├── tools container (nominator contract tools, no network)
   └── evernode-vote (Rust, on the host) ── GraphQL endpoint

cron
   ├── run-elections.sh ── kit scripts          one file per node
   └── evernode vote tick ── evernode-vote      one file per state directory
```

| Part                 | Source                                 | What it does                                                                 |
| -------------------- | -------------------------------------- | ---------------------------------------------------------------------------- |
| evernode             | `evernode/*.py`, entry `cli.main`      | Every command. Owns the node records, the locks and the schedules.           |
| Election kit         | `evernode/vendor/ever-validator/`      | Bash scripts, configs and contract files. Copied into each node's directory. |
| Nominator source     | `evernode/vendor/single-nominator/`    | The contract, its build tool `tools/cells.cjs` and the pinned code hash.     |
| Vote engine          | `vote/` (Rust), binary `evernode-vote` | Checks, pays for and casts config votes. Built by `host setup`.              |
| Node image           | `evernode/assets/ever-node.Dockerfile` | ever-node, console, keygen and ever-cli, built from source.                  |
| Tools image          | `evernode/assets/nominator-tools.Dockerfile` | Node.js with the contract compiler. Built by `nominator prepare`.      |

evernode has no Python dependency outside the standard library.

The kit is a patched copy of the upstream Ever-Validator scripts. Three of its files carry most of
the logic evernode relies on:

- `scripts/env.sh`: the node's settings, every path, and how to call the binaries.
- `scripts/functions.shinc`: reads from the chain and sends to it.
- `scripts/managed_ops.shinc`: the protocol every transfer from the Safe follows
  ([wallet-operations.md](wallet-operations.md)).

## A node at run time

`docker.build_node_compose_config` defines two containers per node, in one Compose project named
`evernode-NAME` with its own network.

| Container       | Image                          | Published port                    | Notes                                         |
| --------------- | ------------------------------ | --------------------------------- | --------------------------------------------- |
| `evernode-NAME` | the node's pinned image id     | the ADNL port, UDP, all addresses | Runs `ever-node` only. Memory ceiling from the record. Open-file limit 1048576. |
| `statsd-NAME`   | `prom/statsd-exporter:v0.26.0` | the metrics port on `127.0.0.1`   | Receives the node's statsd on UDP 9125.        |

- Both containers carry the label `org.evernode.manager=NAME` and restart unless stopped.
- The node's control port, 5888, is not published. The console reaches it inside the container.
- The node container mounts eight directories of the node's workspace at `/ever-node/`:
  `scripts`, `configs`, `contracts`, `node_cfg`, `node_db`, `keys`, `elections`, `logs`.

## How the chain is reached

- **The node's console is the only transport for node operations.** evernode and the kit run
  `docker exec evernode-NAME console -C /ever-node/node_cfg/console.json`. They use it to read
  (`getstatsnew`, `getconfig`, `getaccount`, `getaccountstate`), to send (`sendmessage`) and to
  build elector requests (`election-bid`, `recover_stake`). There is no DApp mode:
  [NODE-1](properties.md#node-1).
- **ever-cli is a local tool.** It runs inside the node container. It builds and signs messages,
  generates phrases and addresses, and runs a contract's getters against an account state that the
  console has saved to a file.
- **The vote engine uses a GraphQL endpoint**, named by the operator. Nothing else does.

## Where code runs

| Code                                         | Runs                                                             |
| -------------------------------------------- | ---------------------------------------------------------------- |
| evernode                                     | on the host, as root                                             |
| Kit scripts started by evernode or by cron   | on the host, as root, in `nodes/NAME/scripts/`                   |
| `init_scripts/R_gen_init_configs.sh`         | once, in a one-off container named `evernode-init-NAME`          |
| console, ever-cli, keygen                    | in the node container, through `docker exec`                     |
| `tools/cells.cjs`                            | in a one-off container from the tools image, with no network     |
| `evernode-vote`                              | on the host, started by evernode                                 |

A kit script on the host and a binary in the container see the same file under two paths:
`nodes/NAME/elections/x.boc` on the host is `/ever-node/elections/x.boc` in the container. `env.sh`
gives every path twice; the container form has the prefix `INPL_`.

evernode runs kit scripts in three ways (`evernode/scripts.py`):

- `execute_validator_script`: a whole kit script on the host. Allowed names are listed in
  `HOST_SCRIPTS`. Output is kept off the terminal
  ([SEC-2](properties.md#sec-2)).
- Inline programs: short bash texts that source `env.sh` and `functions.shinc` and call kit
  functions. They serve reads (`read_depool_state`, `read_solo_stake_state`) and two operations
  (`recover_solo_stake`, `fund_nominator_deployer`).
- `execute_supported_node_script`: the init script, in its one-off container.

## State

Everything evernode owns is under the state directory, `/var/lib/ever-validator` unless
`--state-dir` names another. Its path may hold no whitespace, `:`, `$` or `%`: the election scripts
split paths at whitespace, and Compose and cron cannot take the rest. [state.md](state.md) describes
each file.

```
<state dir>/
  .lock                     registry lock
  locks/                    one lock per node, one per image build
  images/                   one record per managed image
  voting/                   joined proposals, their lock and cron log
  nodes/NAME/
    node.json               the node record
    compose.json            the container definition
    scripts/ configs/ contracts/     the node's copy of the kit
    node_cfg/               node configuration and its keys
    node_db/                the block database
    keys/                   Safe and DePool keys, addresses, seed phrases
    elections/              election files, account states, the election lock
    logs/                   node logs and election logs
    run-elections.sh        what cron starts
```

Outside it evernode writes only `/etc/cron.d/evernode-NAME`, `/etc/cron.d/evernode_vote`, what
`host setup` installs, and temporary directories for image builds.

## Locks

All locks are `flock` locks on files. All are taken without waiting: a held lock is a refusal, not
a queue.

| Lock          | File                                        | Held by                                                          |
| ------------- | ------------------------------------------- | ---------------------------------------------------------------- |
| Node lock     | `locks/NAME.lock`                           | every command that changes a node, and every `wallet`, `depool`, `stake` and `nominator` command ([LOCK-1](properties.md#lock-1)) |
| Registry lock | `.lock`                                     | short checks across nodes: names, ports, addresses, image records |
| Build lock    | `locks/image-KEY.lock`                      | one build of one image record                                    |
| Election lock | `nodes/NAME/elections/evernode-election.lock` | a scheduled run, and every command in [LOCK-2](properties.md#lock-2) |
| Vote lock     | `voting/lock`                               | `vote join`, `vote leave` and each `vote tick`                   |
| Script locks  | `nodes/NAME/elections/SCRIPT.lock`          | a kit script, against a second copy of itself. The election scripts, the setup scripts and `Sign_Trans.sh` take one. |

Read-only node commands (`node status`, `node sync`, `node logs`, `node config`) take no lock.

The order is fixed: node lock, then election lock, then registry lock
([LOCK-4](properties.md#lock-4)).

## Schedules

| Cron file                    | Line                                                        | Written by       |
| ---------------------------- | ----------------------------------------------------------- | ---------------- |
| `/etc/cron.d/evernode-NAME`  | every N minutes, `nodes/NAME/run-elections.sh`              | `election start` |
| `/etc/cron.d/evernode_vote`  | every minute, `evernode --state-dir DIR vote tick`          | `vote join`      |

N is the node's `CRONTAB_INTERVAL`, 10 unless `node setup` set another. A state directory other
than the default gets its own vote file, `evernode_vote_` and 8 hex digits. Both files start with
the line `# Managed by evernode`; evernode refuses to replace or remove a file without it.

## Trust and exposure

- evernode runs as root and trusts only files that root owns and nobody else can read or write
  ([SEC-1](properties.md#sec-1)).
- Secrets on the server: the node's own keys in `node_cfg/`, the Safe's and the DePool's seed
  phrases and key pairs in `keys/`. A nominator's owner key is never on the server
  ([SEC-3](properties.md#sec-3)).
- evernode changes only containers that carry its label for that node
  ([SEC-5](properties.md#sec-5)).
- The host's firewall is not touched. Opening the ADNL port is the operator's job.

## Module map

| Module               | Owns                                                                    |
| -------------------- | ----------------------------------------------------------------------- |
| `arguments.py`       | every command and flag                                                  |
| `cli.py`             | routing, and the commands that are a few lines long                     |
| `process.py`         | the one way a child process is started and cleaned up                   |
| `storage.py`         | the state directory: locks, private paths, atomic writes                |
| `configuration.py`   | the node record's validation and the values rendered into `env.sh`      |
| `docker.py`          | container definition, ownership check, free ports and names             |
| `images.py`          | managed image records and builds                                        |
| `host.py`            | host check and host setup                                               |
| `creation.py`        | `node create`                                                           |
| `provisioning.py`    | the node's workspace, its kit copy and `node sync-scripts`              |
| `nodes.py`           | start, stop, remove, status, sync state, duty guards, `lsync`, `update` |
| `role.py`            | `node setup`                                                            |
| `setup_actions.py`   | the `wallet`, `depool`, `stake` and `nominator` commands: locks, order  |
| `wallets.py`         | Safe and DePool setup stages, DePool commands                           |
| `stake.py`           | the recorded stake of a solo or nominator validator                     |
| `nominator.py`       | the nominator contract: build, deploy, verify, owner requests           |
| `scripts.py`         | running kit scripts and inline kit programs                             |
| `elections.py`       | the election schedule, its programs and the election lock               |
| `voting.py`          | the `vote` group: what evernode tells the engine                        |
| `vote_schedule.py`   | joined proposals, their store and the cron tick                         |
| `log_level.py`, `resources.py`, `disk_usage.py`, `ui.py` | log level, resource report, prompts |
