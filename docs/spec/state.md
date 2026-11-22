# State

What is stored where: the node record, its settings and setup stages, and every file evernode or
the kit keeps. evernode has no database. Each node is one directory, and the node record is one
JSON file in it.

## The node record

`nodes/NAME/node.json`. `configuration.validate_saved_node_config` checks it on every load and
every save ([STATE-1](properties.md#state-1)).

| Field                               | Value                                                | Set by                         |
| ----------------------------------- | ---------------------------------------------------- | ------------------------------ |
| `schema_version`                    | `1`                                                  | `node create`                  |
| `name`                              | lowercase letter, then letters, digits, hyphens; 40 at most | `node create`           |
| `container_name`, `statsd`          | `evernode-NAME`, `statsd-NAME`                       | `node create`                  |
| `compose_project`                   | `evernode-NAME`                                      | `node create`                  |
| `network`                           | `main` or `devnet`                                   | `node create`                  |
| `ip`                                | the node's public IPv4                               | `node create`                  |
| `memory`                            | a number with `M` or `G`                             | `node create`                  |
| `adnl_port`, `metrics_port`         | 1024 to 65535                                        | `node create`                  |
| `image`, `image_id`                 | the image tag and its `sha256:` id                   | `node create`, `node update`   |
| `image_rollback`                    | the image a `node update` replaced                   | `node update`                  |
| `phase`                             | see [Phase](#phase)                                  | `node create`, `start`, `remove` |
| `prepare_stage`                     | `reserved`, `source-captured`, `complete`            | `node create`                  |
| `initialized`                       | the node's own keys and config exist                 | first start                    |
| `source_spec`, `project_source`     | where the node's kit copy comes from                 | `node create`, `sync-scripts`  |
| `project_commit`, `source_hashes`   | the kit version copied and its fingerprints          | `node create`, `sync-scripts`  |
| `validator_type`                    | `depool`, `solo` or `nominator`                      | `node setup`                   |
| `settings`                          | see [Settings](#settings)                            | `node setup`, `stake set`, `nominator prepare` |
| `wallet`                            | see [Wallet profile](#wallet-profile)                | `node setup`, `wallet create`, `depool prepare` |
| `setup_stage`                       | see [Setup stages](#setup-stages)                    | each setup command             |
| `nominator`, `nominator_tools_image`, `nominator_source` | the prepared contract and its tools | `nominator prepare`    |

A node has a *role* once it has a `validator_type` or a `wallet` (`configuration.has_role`). A
record without `validator_type` but with a wallet is a DePool validator; such records predate the
type choice.

### Phase

| Phase           | Meaning                                                        |
| --------------- | -------------------------------------------------------------- |
| `preparing`     | The workspace is reserved but not complete. Only `node create --resume` continues it. |
| `prepared`      | The workspace is complete. The node has not answered its console yet. |
| `console-ready` | The node has answered its console at least once.               |
| `removed`       | `node remove` took the containers away. Keys and configuration remain. |

### Settings

`settings` holds only keys of `configuration.DEFAULT_ENVIRONMENT`. A missing key takes the default.

| Key                         | Default       | Allowed            | Meaning                                                      |
| --------------------------- | ------------- | ------------------ | ------------------------------------------------------------ |
| `NODE_WC`                   | `0`           | `0`, `-1`          | Workchain of a DePool validator's Safe.                      |
| `CRONTAB_INTERVAL`          | `10`          | 1 to 59            | Minutes between scheduled election runs.                     |
| `DePool_TYPE`               | `EverX`       | `EverX`, `StEver`  | Which DePool contract files the kit uses.                    |
| `ValidatorAssuranceT`       | `50000`       | 0 or more          | Tokens; a new DePool's validator assurance and first stake.  |
| `MinStakeT`                 | `10`          | 0 or more          | Tokens; a new DePool's minimum participant stake.            |
| `ParticipantRewardFraction` | `65`          | 0 to 100           | Percent of the reward a new DePool gives its participants.   |
| `BalanceThresholdT`         | `20`          | 0 or more          | Tokens; sizes the funding a new DePool needs at deployment.  |
| `MSIG_FIX_STAKE`            | `0`           | 0 or more          | Tokens a solo or nominator validator stakes per election. 0 means none recorded. |
| `VAL_ACC_RESERVED`          | `50`          | 0 or more          | Tokens `stake set` keeps out of a computed stake.            |
| `MAX_FACTOR`                | `3`           | 1 to 100           | Written to `env.sh`; no script uses it. See the note below.  |
| `NOMINATOR_ADDR`            | empty         |                    | The nominator contract's address.                            |
| `NOMINATOR_OWNER`           | empty         |                    | The cold owner's address.                                    |
| `NOMINATOR_OP_FEE`          | `2000000000`  |                    | Nanotokens the Safe attaches to each instruction to the contract. |
| `NOMINATOR_RESERVE`         | `1000000000`  |                    | Nanotokens the contract keeps and never stakes.              |
| `NOMINATOR_DEPLOY_VALUE`    | `10000000000` |                    | Nanotokens the deployer passes to the new contract.          |
| `NOMINATOR_DEPLOY_MARGIN`   | `1000000000`  |                    | Nanotokens added to the deployer's funding for fees.         |
| `NOMINATOR_TOOLS_IMAGE`     | empty         |                    | The tools image the contract was compiled with.              |

No command changes `VAL_ACC_RESERVED`, `MAX_FACTOR` or the four nominator amounts.

The max factor a bid carries is not this setting. It is `max_factor` in `node_cfg/console.json`,
which is 3 from the kit's template and which the console writes into the elector request.

### Wallet profile

`wallet` has exactly five keys (`wallets.validate_wallet_profile`):

| Key                   | Value                                                              |
| --------------------- | ------------------------------------------------------------------ |
| `mode`                | `new`: evernode generates the Safe. `import`: the Safe exists.     |
| `custodians`          | 1 to 31                                                            |
| `required_signatures` | 1 to `custodians`                                                  |
| `wallet_address`      | the Safe; required for `import`, filled by `wallet create` for `new` |
| `depool_address`      | DePool validators only; required for `import`, filled by `depool prepare` for `new` |

### Setup stages

`setup_stage` says how far setup has come. Each setup command requires one stage and one mode and
moves to the next ([setup.md](setup.md)).

| Stage                 | Reached by                                          | Next command                                   |
| --------------------- | --------------------------------------------------- | ---------------------------------------------- |
| `preparing`           | `node create`                                       | the node's first start moves it on             |
| `node-started`        | first start                                         | `node setup`, then `wallet create` or `wallet recover` |
| `wallet-created`      | `wallet create`                                     | `wallet deploy`                                |
| `wallet-deployed`     | `wallet deploy`                                     | `depool prepare`, `stake set`, or `nominator prepare` |
| `depool-prepared`     | `depool prepare`                                    | `depool deploy`                                |
| `depool-deployed`     | `depool deploy`                                     | `depool stake-initial`                         |
| `wallet-recovered`    | `wallet recover`                                    | `wallet verify`                                |
| `wallet-verified`     | `wallet verify`, DePool validator                   | `depool verify`                                |
| `imported`            | `depool verify`; `wallet verify`, solo or nominator | `election start`, `stake set`, or `nominator prepare` |
| `nominator-prepared`  | `nominator prepare`                                 | `nominator deploy`                             |
| `nominator-deployed`  | `nominator deploy`                                  | `nominator verify`                             |
| `nominator-verified`  | `nominator verify`                                  | `stake set`                                    |
| `ready-for-elections` | `depool stake-initial`; `stake set`                 | `election start`                               |

`wallets.next_setup_command` turns a stage into the command `node status` shows.

## The node's copy of the kit

`scripts/`, `configs/` and `contracts/` in the node's directory are a copy of the node's kit
source, made at `node create` and replaced only by `node sync-scripts`
([STATE-4](properties.md#state-4)). `.prepared-source/` keeps the snapshot the copy was made from,
with its fingerprints in `manifest.json`. `.script-backups/` keeps what `sync-scripts` replaced.

### `scripts/env.sh`

The kit's `env.sh` with the node's values written in. `configuration.build_node_environment` is the
only source of those values, and `configuration.write_node_environment` writes them.

| Written                                         | When                                                |
| ----------------------------------------------- | --------------------------------------------------- |
| every managed value, into the kit's template    | `node create`, `node sync-scripts`                  |
| every managed value, in place                   | `node setup`, `stake set`                           |
| `NOMINATOR_ADDR`, `NOMINATOR_TOOLS_IMAGE`       | `nominator prepare`                                 |

The managed values are the record's fields and settings, plus fixed ones: `RUN_MODE=docker`,
`NODE_ROLE=validator`, `FORCE_USE_DAPP=false`, `RCONSOLE_PORT=5888`, both auto-update switches
`false`. `STAKE_MODE` is `depool`, `msig` (solo) or `nominator`.

The kit's own defaults stay as the template has them. The ones the logic depends on:

| Variable               | Value | Meaning                                                        |
| ---------------------- | ----- | -------------------------------------------------------------- |
| `MSG_LIFETIME`         | `60`  | Seconds every message the kit signs lives.                     |
| `BID_ATTEMPTS_MAX`     | `3`   | Bids per election that may fail to register.                   |
| `NODE_SYNC_TOLERANCE`  | `20`  | Seconds the node may lag before a kit script refuses.          |
| `LC_Send_MSG_Timeout`  | `10`  | Seconds the kit pauses after it hands a message to the node.   |

A value edited by hand in a node's `env.sh` lasts until the next `node sync-scripts`, which writes
the file again from the template.

## Keys

`nodes/NAME/keys/`.

| File                                    | Holds                                                   |
| --------------------------------------- | ------------------------------------------------------- |
| `NAME.addr`                             | the Safe's address                                      |
| `NAME_1.keys.json` … `NAME_N.keys.json` | one key pair per custodian                              |
| `NAME.secret.json`                      | the kit's own summary                                   |
| `depool.addr`, `depool.keys.json`       | the DePool's address and deployment key                 |
| `proxy0.addr`, `proxy1.addr`            | the DePool's two proxies, written by the election run   |
| `NAME_msig_deploy.boc`, `NAME_depool_deploy.boc` | the deploy messages, with their logs           |

The names `depool`, `proxy0` and `proxy1` are why no node may have them
([STATE-6](properties.md#state-6)).

`node_cfg/` holds the node's own configuration: `config.json` with the validator key ring,
`console.json` with the console's client key, `log_cfg.yml`, the network's global config and
ever-cli's config file.

## Elections directory

`nodes/NAME/elections/` is the kit's working directory, shared by scheduled runs and operator
commands.

| File                                  | Holds                                                           |
| ------------------------------------- | --------------------------------------------------------------- |
| `evernode-election.lock`              | the election lock                                               |
| `SCRIPT.lock`                         | each kit script's lock against a second copy of itself          |
| `ACCOUNT.boc`                         | the last fetched state of an account, named by its address hex  |
| `stakeOrdinary.boc`, `withdraw-part.boc`, `withdraw-all.boc`, `cancel_withdrawal.boc`, `replenish.boc`, `recover-msg.boc`, `nominator-fund.boc`, `ACCOUNT_topup.boc` | the last message of each kind an operation built |
| `last-preparation.log`                | the last preparation's output, solo and nominator sequence      |
| `unsent_transactions.log`             | counts of pending Safe transactions, from each DePool operation |
| `nominator/`                          | the prepared contract: `manifest.json`, `code.boc`, `init.boc`, `factory-deploy.boc`, owner request bodies |
| `ELECTION/`                           | one directory per election, named by the election id            |

In `ELECTION/`, with the election id as prefix:

| File                              | Holds                                                               |
| --------------------------------- | ------------------------------------------------------------------- |
| `ID.log`                          | what each run decided                                               |
| `ID_query.boc`                    | the elector request. Made once per election and reused ([ELECT-1](properties.md#elect-1)). |
| `ID-bid.log`                      | the console's output when it made the request                       |
| `ID_stake.boc`                    | nominator only: the request with the stake written in               |
| `ID_vaidator-query-msg.boc`       | the Safe's message that carries the bid                             |
| `ID_bid-attempts.txt`             | one line per bid that was sent and did not register                 |
| `ID_bid-stopped.txt`              | why no further bid is sent in this election                         |
| `ID_elector-confirmed-bid.txt`    | the bid is registered; later runs stop at this file                 |
| `recover-query.boc`, `recover-msg.boc`, `tik-msg.boc`, `depool_replenish.boc` | preparation's messages         |
| `proxy0_topup.log`, `proxy1_topup.log` | the output of a proxy top-up                                   |
| `NEXTID_proxy.id`                 | written by every bid under the next election's id; nothing reads it |

With no election open, preparation writes its files into `elections/` itself.

Nothing removes election directories.

## Logs

`nodes/NAME/logs/`.

| File                                    | Written by                                                    |
| --------------------------------------- | ------------------------------------------------------------- |
| `node/node.log`                         | ever-node, by `node_cfg/log_cfg.yml`                          |
| `node/stdout.log`, `node/stderr.log`    | the node container's command                                  |
| `validator/election-cron.log`           | every scheduled run, whole output                             |
| `validator/transactions.log`            | the election scripts: counts of pending Safe transactions     |
| `validator/stop-at-block.log`           | a scheduled stop: a JSON line a minute, and its result        |
| `validator/SCRIPT-failed-TIME.log`      | evernode, when a kit script it ran failed ([SEC-2](properties.md#sec-2)) |

## Other files in the node's directory

| File                                        | Holds                                               |
| ------------------------------------------- | --------------------------------------------------- |
| `compose.json`                              | the container definition, rewritten at each start of a stopped node |
| `run-elections.sh`                          | the runner cron starts                              |
| `run-election-sequence.sh`, `run-solo-election-sequence.sh` | the sequence the runner runs; one of the two exists |
| `lsync.json`                                | the last local sync into this node                  |
| `stop-at-block.json`                        | a scheduled stop: `version`, `after_block`, `timeout`, `force`, and `seen_below_at`, the Unix time a reading last showed the node below the block |
| `stop-at-block.lock`                        | the stop check lock                                 |
| `.node_db-lsync-*`                          | a local sync's working copies; a leftover is named in a warning |

## Image records

`images/KEY.json`, one per managed image (`images.build_managed_image`).

| Field            | Value                                                                    |
| ---------------- | ------------------------------------------------------------------------ |
| `schema_version` | `1`                                                                      |
| `inputs`         | repositories, commits, Rust version, node features, the recipe's hash    |
| `key`            | SHA-256 of `inputs`; the file's name                                     |
| `image`          | the tag: the image repository and the first 16 digits of `key`           |
| `image_id`       | the built image's `sha256:` id                                           |

## Vote store

`voting/proposals.json` (`vote_schedule.VoteStore`): `{"version": 1, "proposals": [...]}`. One
entry per proposal joined on this host, open or ended.

| Field                                   | Value                                                         |
| --------------------------------------- | ------------------------------------------------------------- |
| `proposal`                              | the proposal's hash                                           |
| `state`                                 | `open`, `applied`, `not applied` or `left`                    |
| `joined_at`, `ended_at`                 | Unix times                                                    |
| `nodes`, `keyrings`                     | the managed nodes and other key rings that vote               |
| `endpoint`                              | the GraphQL endpoint; removed when the proposal ends          |
| `next_pass_at`                          | when the next pass is due                                     |
| `votes`                                 | every vote counted: round, node, validator index, key, transaction, times |
| `param`, `target`, `config_version`     | what later passes need to recognise the proposal's end        |
| `change`, `change_lines`, `critical`, `progress`, `earliest_acceptance`, `round_end` | the last report (a pass's, or `vote status`'s read), for `vote status` and `vote history` |
| `last_pass`                             | when the last pass ran and its result or error                |

`voting/lock` is the vote lock. `voting/cron.log` is the output of every `vote tick`.
