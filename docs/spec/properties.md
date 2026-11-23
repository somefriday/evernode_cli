# Properties

The rules that hold across features. Breaking one is a bug. Each has an id; feature files, code
comments, tests and bug reports refer to it by that id. The format is in [rules.md](rules.md).

| Id                    | Rule in a line                                                        |
| --------------------- | --------------------------------------------------------------------- |
| [SEC-1](#sec-1)       | Managed files are root's and private.                                 |
| [SEC-2](#sec-2)       | A seed phrase never leaves its file or the terminal.                  |
| [SEC-3](#sec-3)       | A nominator's owner key is never on the server.                       |
| [SEC-4](#sec-4)       | The vote engine gets its endpoint and phrases through a pipe.         |
| [SEC-5](#sec-5)       | Only labelled containers are touched.                                 |
| [SEC-6](#sec-6)       | A node's identity is generated once.                                  |
| [STATE-1](#state-1)   | The node record is checked on every load and save.                    |
| [STATE-2](#state-2)   | A file another process reads is replaced whole.                       |
| [STATE-3](#state-3)   | A node runs a pinned image.                                           |
| [STATE-4](#state-4)   | A node's kit copy changes only through `node sync-scripts`.           |
| [STATE-5](#state-5)   | Names, ports and addresses belong to one node.                        |
| [STATE-6](#state-6)   | Three node names are reserved.                                        |
| [LOCK-1](#lock-1)     | One command per node at a time.                                       |
| [LOCK-2](#lock-2)     | The election lock covers every use of the Safe.                       |
| [LOCK-3](#lock-3)     | Questions come before the election lock.                              |
| [LOCK-4](#lock-4)     | Locks are taken in one order.                                         |
| [LOCK-5](#lock-5)     | A scheduled run that cannot start is skipped and says so.             |
| [NODE-1](#node-1)     | The node's console is the only transport.                             |
| [NODE-2](#node-2)     | A node in a validator set is not stopped by accident.                 |
| [NODE-3](#node-3)     | A node with an undecided bid is not stopped by accident.              |
| [NODE-4](#node-4)     | Maintenance does not run under a schedule.                            |
| [NODE-5](#node-5)     | Stopping a node removes its schedule; only `election start` installs one. |
| [NODE-6](#node-6)     | A scheduled stop stops a node once, and only after its block is applied. |
| [SETUP-1](#setup-1)   | Setup commands run in stage order.                                    |
| [SETUP-2](#setup-2)   | A stage is recorded only after the chain shows it.                    |
| [SETUP-3](#setup-3)   | A role is fixed once wallet setup begins.                             |
| [SETUP-4](#setup-4)   | The validator type decides the Safe's workchain.                      |
| [SETUP-5](#setup-5)   | A solo or nominator stake is a recorded number above the minimum.     |
| [SETUP-6](#setup-6)   | A schedule is installed only for a finished setup.                    |
| [OP-1](#op-1)         | Every message lives 60 s.                                             |
| [OP-2](#op-2)         | Success is the operation's effect on chain.                           |
| [OP-3](#op-3)         | A reported failure is final.                                          |
| [OP-4](#op-4)         | A transaction is recognised by its content and its creator.           |
| [OP-5](#op-5)         | A pending transaction is finished, not doubled.                       |
| [OP-6](#op-6)         | Signatures go one at a time, inside the transaction's life.           |
| [OP-7](#op-7)         | No submit into a full queue.                                          |
| [OP-8](#op-8)         | A read that failed is not an answer.                                  |
| [OP-9](#op-9)         | Only a named transaction is confirmed by hand.                        |
| [ELECT-1](#elect-1)   | One request per election.                                             |
| [ELECT-2](#elect-2)   | A registered bid is never sent again.                                 |
| [ELECT-3](#elect-3)   | A bid counts when the elector lists the node.                         |
| [ELECT-4](#elect-4)   | Retries are bounded.                                                  |
| [ELECT-5](#elect-5)   | A failed preparation stops a DePool run, not a solo run.              |
| [ELECT-6](#elect-6)   | No bid beside a pending transaction.                                  |
| [ELECT-7](#elect-7)   | A proxy is funded before it can freeze.                               |
| [ELECT-8](#elect-8)   | No stake moves to or from the elector while a proxy is not active.    |
| [ELECT-9](#elect-9)   | No recovery or bid while the contract's hot wallet is not this Safe.  |
| [VOTE-1](#vote-1)     | Managed nodes vote only through `vote join`.                          |
| [VOTE-2](#vote-2)     | A payment is asked about first.                                       |
| [VOTE-3](#vote-3)     | One vote pass at a time.                                              |
| [VOTE-4](#vote-4)     | A counted vote is recorded at once.                                   |
| [VOTE-5](#vote-5)     | The engine's version is pinned.                                       |

## Access and secrets

### SEC-1

**Managed files are root's and private.**

- **Rule.** The state directory and every directory above it must be root's and not writable by
  group or others. A node's directory, its record, its scripts and its key files must be owned by
  the user running evernode, carry no permission for group or others, and be a regular file or a
  directory, never a symlink.
- **Why.** evernode runs as root and hands these files to Docker and to bash. A file another user
  can replace would run as root.
- **Enforced by.** `storage.validate_private_path`, `NodeStateStore.validate_state_root`,
  `storage.restrict_workspace_permissions`.
- **Checked by.** `tests/test_provisioning.py`.

### SEC-2

**A seed phrase never leaves its file or the terminal.**

- **Rule.** A seed phrase must never be a command argument or an environment variable. It is typed
  at a hidden prompt or generated in place. Only the command that generated it prints it, and only
  to an interactive terminal. A kit script's output is never shown on the terminal: a failed
  script's output goes to a private log with any phrase masked, and only the lines the script
  marked as diagnostics are shown.
- **Why.** Arguments and environment are visible to every user of the host, and the kit's setup
  scripts print phrases.
- **Enforced by.** `wallets.read_import_seeds`, `setup_actions.handle_setup_action`,
  `scripts.execute_validator_script`, `scripts._record_script_failure`,
  `scripts.script_diagnostics`; `Msig-prep.sh` and `DePool-prep.sh`, which pipe the phrase to
  `evernode-vote key-from-phrase`; `voting.REFUSED_OPTIONS`.
- **Checked by.** `tests/test_key_scripts.py`, `tests/test_console_only.py`.

### SEC-3

**A nominator's owner key is never on the server.**

- **Rule.** evernode must never ask for, store or use the key of a nominator contract's owner. The
  owner's address must not be a Safe, a DePool or a nominator contract of any node on this server,
  and must differ from the node's own Safe. Owner operations are only prepared, as a message body
  to sign elsewhere.
- **Why.** That key withdraws the stake. Keeping it off the server is the reason this validator
  type exists.
- **Enforced by.** `wallets.ensure_owner_is_external`, `nominator.prepare`,
  `nominator._owner_request`.
- **Checked by.** `tests/test_nominator.py`.

### SEC-4

**The vote engine gets its endpoint and phrases through a pipe.**

- **Rule.** The GraphQL endpoint, a payer's seed phrase and the paths of key files reach
  `evernode-vote` only in the description evernode writes into a pipe. A joined proposal's endpoint
  is stored only while the proposal is open.
- **Why.** An endpoint's URL can carry a project key, and arguments and environment are readable
  by other users.
- **Enforced by.** `voting.run_engine`, `vote_schedule.run_pass`, `vote_schedule.end`.
- **Checked by.** `tests/test_voting.py`, `tests/test_vote_schedule.py`.

### SEC-5

**Only labelled containers are touched.**

- **Rule.** evernode must stop, remove or replace a container only if it carries the label
  `org.evernode.manager` with the node's name.
- **Why.** A container with a managed node's name may belong to something else.
- **Enforced by.** `docker.inspect_managed_container`.
- **Checked by.** `tests/test_resources.py`.

### SEC-6

**A node's identity is generated once.**

- **Rule.** A node's own keys and configuration are generated at its first start and never again.
  A node that is not marked initialized but has files in `node_cfg/` must be refused, not
  initialized again.
- **Why.** Generating again would replace the node's network identity and its validator key ring.
- **Enforced by.** `nodes.start_node`, `provisioning.prepare_node_workspace`.
- **Checked by.** `tests/test_node_management.py`, `tests/test_provisioning.py`.

## State

### STATE-1

**The node record is checked on every load and save.**

- **Rule.** A node record must pass `validate_saved_node_config` when it is read and before it is
  written. A record that fails stops the command.
- **Why.** Its values become container names, paths and shell values.
- **Enforced by.** `configuration.validate_saved_node_config`, called by
  `NodeStateStore.load_node_config` and `NodeStateStore.save_node_config`.
- **Checked by.** `tests/test_provisioning.py`, `tests/test_container_naming.py`.

### STATE-2

**A file another process reads is replaced whole.**

- **Rule.** These files must be written under a temporary name and then renamed: the node record,
  `compose.json`, `env.sh`, `log_cfg.yml`, image records, the vote store, the cron files, the
  runner and the sequence, and every account state file in `elections/`. `console.json` must be
  replaced only by a file that still holds the console's client key.
- **Why.** Scheduled runs and commands read these files at any moment. A missing or half-written
  state file was read as an answer, and a failed rewrite of `console.json` would lose the key that
  reaches the node.
- **Enforced by.** `storage.write_json_atomically`, `storage.write_private_text_atomically`,
  `elections._replace_program`, `Get_SC_current_state` in `functions.shinc`,
  `take_part_in_elections.sh`.
- **Checked by.** `tests/test_account_reads.py`, `tests/test_election_verdict.py`.

### STATE-3

**A node runs a pinned image.**

- **Rule.** A node's container runs the image id in its record, never a tag. An image record is
  used only while its tag still names its id. An image must not be removed while a container uses
  it, or while the record or the rollback of a node that has not been removed names it.
- **Why.** A tag can be moved. A node must restart into the same image it was checked with.
- **Enforced by.** `docker.build_node_compose_config`, `images.inspect_recorded_image`,
  `images.remove_managed_image`.
- **Checked by.** `tests/test_image_selection.py`, `tests/test_node_management.py`,
  `tests/test_review_round2.py`.

### STATE-4

**A node's kit copy changes only through `node sync-scripts`.**

- **Rule.** `scripts/`, `configs/` and `contracts/` in a node's directory are replaced only by
  `node sync-scripts`, which shows what changes, keeps a backup and checks the result against the
  rendered source. Updating evernode does not change them. The one exception is `env.sh`, which a
  command also rewrites when it changes a setting the file carries.
- **Why.** A node's schedule must not get different scripts without the operator choosing it.
- **Enforced by.** `provisioning.refresh_node_scripts`, `provisioning._install_rendered_files`.
- **Checked by.** `tests/test_depool_commands.py`.

### STATE-5

**Names, ports and addresses belong to one node.**

- **Rule.** A node name, a container name, a host port, a Safe address and a DePool address must
  belong to at most one managed node. The check and the save happen under the registry lock.
- **Why.** Two nodes on one port cannot both run, and two nodes signing from one Safe spend each
  other's stake.
- **Enforced by.** `docker.ensure_container_names_available`, `docker.select_available_node_port`,
  `provisioning.provision_node_workspace`, `wallets.ensure_unassigned`, `role.handle_node_setup`.
- **Checked by.** `tests/test_container_naming.py` for names. No test for addresses.

### STATE-6

**Three node names are reserved.**

- **Rule.** No node is named `depool`, `proxy0` or `proxy1`.
- **Why.** The kit keeps `depool.addr`, `proxy0.addr` and `proxy1.addr` beside the node's own
  `NAME.addr` in `keys/`.
- **Enforced by.** `creation.handle_node_creation`.
- **Checked by.** `tests/test_node_setup.py`.

## Locks

### LOCK-1

**One command per node at a time.**

- **Rule.** A command that changes a node, and every `wallet`, `depool`, `stake` and `nominator`
  command, holds the node lock. A second one is refused at once. It does not wait.
- **Why.** Two commands would read and save the same record and stage.
- **Enforced by.** `NodeStateStore.acquire_operation_lock`.
- **Checked by.** `tests/test_provisioning.py`.

### LOCK-2

**The election lock covers every use of the Safe.**

- **Rule.** Each of these holds the node's election lock for as long as it works, and is refused
  or skipped while another holds it:
  - a scheduled election run;
  - `depool stake-initial`, `depool stake`, `depool withdraw`, `depool replenish`,
    `depool confirm`;
  - `stake set`, `stake recover`, `stake confirm`;
  - `nominator prepare`, `nominator deploy`, `nominator submit`;
  - `wallet send`;
  - `node restart`, `node update`, and `node lsync` for both nodes;
  - the install step of `node sync-scripts`;
  - the payment of `vote propose --pay-from-safe`.
- **Why.** Two signers on one Safe race for the same queue and the same replay protection. A run
  also reads `env.sh` and the scripts as it goes, so they must not change under it.
- **Enforced by.** `setup_actions.ELECTION_LOCKED_ACTIONS`, `elections.hold_election_lock`,
  `nodes.apply_node_lifecycle_operation`, `nodes.update_node_image`, `nodes._local_sync_locks`,
  `cli.handle_script_refresh`, `voting.pay_and_propose`, and the runner written by
  `elections.write_election_programs`.
- **Checked by.** `tests/test_depool_commands.py`, `tests/test_node_management.py`,
  `tests/test_voting.py`, `tests/test_wallet_send.py`.

Read-only commands (`depool info`, `stake status`, `nominator info`) do not take it.

### LOCK-3

**Questions come before the election lock.**

- **Rule.** A command must ask every question, and show every menu, before it takes the election
  lock.
- **Why.** A prompt left open with the lock held would skip every scheduled run until someone
  answers.
- **Enforced by.** `setup_actions.handle_setup_action`, `cli.handle_script_refresh`,
  `voting.pay_and_propose`; in the engine, `propose` in `vote/src/campaign.rs`.
- **Checked by.** `tests/test_depool_commands.py`, `tests/test_voting.py`,
  `tests/test_wallet_send.py`.

### LOCK-4

**Locks are taken in one order.**

- **Rule.** For one node the node lock comes first, then its election lock. The registry lock
  comes last and is held only for a check and a save. `node lsync` takes its two nodes' locks in
  name order. No lock ever waits.
- **Why.** One order and no waiting leave no way to deadlock.
- **Enforced by.** `NodeStateStore.acquire_operation_lock`, `nodes._local_sync_locks`, and the
  order of the `with` blocks in each command.
- **Checked by.** No test.

### LOCK-5

**A scheduled run that cannot start is skipped and says so.**

- **Rule.** Scheduled runs of one node never overlap. A run that cannot take the election lock
  ends at once and writes a line with the word `skipped` to `election-cron.log`.
- **Why.** A run that waited would pile up behind a long one. A silent skip hides a lock that
  something forgot to release.
- **Enforced by.** The runner written by `elections.write_election_programs`.
- **Checked by.** `tests/test_solo_validation.py`.

## Node and duty

### NODE-1

**The node's console is the only transport.**

- **Rule.** Every read from the chain and every message for a node goes through that node's own
  console. There is no fallback to a public endpoint. Network config votes are not a node's: they
  go through a GraphQL endpoint ([voting.md](voting.md)). A command or script that needs the chain
  must refuse when the node does not answer or lags:
  - evernode: the status must be `synchronization_by_blocks` or `synchronization_finished` and
    both lags within 10 s. It reads up to three times, 5 s apart.
  - the kit: both lags within 20 s.
  - a server clock more than that limit behind the newest block is refused as well.
- **Why.** A managed node may be on a private network. An answer from someone else's endpoint
  hides exactly the fault the operator must fix.
- **Enforced by.** `scripts.require_node_console`, `setup_actions._require_setup_node`,
  `nodes.is_node_synchronized`, `Require_Node_Console` in `functions.shinc`,
  `configuration.build_node_environment`.
- **Checked by.** `tests/test_console_only.py`.

### NODE-2

**A node in a validator set is not stopped by accident.**

- **Rule.** `node stop`, `node restart` and `node remove` must refuse, unless `--force`, a node
  that reports itself in the current or the next validator set. A synchronized node that reports
  nothing about its membership is refused too. A node that does not answer, or is not
  synchronized, is not refused.
- **Why.** A current-set member that stops misses its blocks. A next-set member is needed for its
  session to reach consensus.
- **Enforced by.** `nodes.validator_duty`, `nodes._refuse_while_validating`.
- **Checked by.** `tests/test_node_management.py`, `tests/test_depool_commands.py`.

### NODE-3

**A node with an undecided bid is not stopped by accident.**

- **Rule.** `node stop` and `node remove` must refuse, unless `--force`, a node that holds a
  validator key for a round that starts in the future while the next validator set (config param
  36) is not published.
- **Why.** Elected while stopped, the node misses that round.
- **Enforced by.** `nodes.pending_election`, `nodes._refuse_with_pending_bid`.
- **Checked by.** `tests/test_node_management.py`.

### NODE-4

**Maintenance does not run under a schedule.**

- **Rule.** `node restart`, `node update` and `node lsync` must refuse a node whose election
  schedule is installed. `node update` also refuses a node in a validator set. `node lsync`
  requires a source that reports itself outside both sets.
- **Why.** A run that starts in the middle would find its node stopped, or its database being
  replaced.
- **Enforced by.** `nodes.apply_node_lifecycle_operation`, `nodes.image_maintenance_blockers`,
  `nodes._local_sync_preflight`.
- **Checked by.** `tests/test_node_management.py`.

### NODE-5

**Stopping a node removes its schedule; only `election start` installs one.**

- **Rule.** `node stop` and `node remove` remove the node's election schedule. No command but
  `election start` installs it.
- **Why.** A schedule left behind runs against a stopped node. A schedule that came back by itself
  would bid without the operator's decision.
- **Enforced by.** `nodes.stop_node`, `elections.disable_node_election_schedule`.
- **Checked by.** `tests/test_node_management.py`.

### NODE-6

**A scheduled stop stops a node once, and only after its block is applied.**

- **Rule.** `node stop --after-block N` must find the node below masterchain block N when it
  schedules the stop. The stop must not begin until the console has reported block N and every
  shard block N lists as applied, and 30 s have passed since. A node found at or past N that was
  last seen below N more than 600 s ago must not be stopped: the scheduled stop ends with a
  failure. The stop is tried once. `--cancel` and every other stop of the node must end the
  scheduled stop, and a stop that was ended must not be carried out. The wait must hold no node
  lock and no election lock.
- **Why.** The stopped node is read to export the state of block N. A node stopped earlier does
  not hold the block, a node already past N may have deleted its state, and a node reports from
  memory, so it needs time to write its block records. A stop carried out late, or after a
  cancel, stops a node for nothing.
- **Enforced by.** `stop_schedule.schedule`, `stop_schedule.BlockWait`, `stop_schedule.check`,
  `stop_schedule.MISSED_SECONDS`, `stop_schedule.remove_cron`, `nodes.stop_node`.
- **Checked by.** `tests/test_stop_schedule.py`.

## Setup

### SETUP-1

**Setup commands run in stage order.**

- **Rule.** Each `wallet` and `depool` setup command runs only at its own setup stage and for its
  own wallet mode. Each `depool`, `stake` and `nominator` command runs only for its own validator
  type. The nominator setup commands are ordered by what each needs: `nominator deploy` needs the
  prepared contract, and `nominator verify` needs the active one. A setup command moves the stage
  forward only from the stages before its own. The table is in
  [state.md](state.md#setup-stages).
- **Why.** Each step uses what the step before it made and checked.
- **Enforced by.** `wallets._require_stage`, `wallets.require_validator_type`,
  `nominator._require_nominator`, `nominator._manifest`.
- **Checked by.** `tests/test_solo_validation.py`, `tests/test_nominator.py`.

### SETUP-2

**A stage is recorded only after the chain shows it.**

- **Rule.** A setup stage must be saved only after the chain shows what the stage claims:
  - `wallet-deployed`: the Safe is active, and its custodians and threshold are this node's;
  - `wallet-recovered`: the Safe's custodians are the keys derived from the typed phrases;
  - `depool-deployed`: the DePool is active and names this node's Safe as its validator wallet;
  - `imported`, for a DePool validator: the DePool names this node's Safe;
  - `nominator-verified`: the contract is active with the pinned code hash, this owner and this
    Safe.
- **Why.** An active account is not yet a working contract, and a Safe's address fixes only its
  code and its first key.
- **Enforced by.** `wallets.wallet_deploy`, `wallets.require_own_safe`, `wallets.wallet_recover`,
  `wallets.depool_deploy`, `wallets.require_constructed_depool`, `wallets.depool_verify`,
  `nominator.verify`.
- **Checked by.** `tests/test_focused_features.py`, `tests/test_nominator.py`.

### SETUP-3

**A role is fixed once wallet setup begins.**

- **Rule.** `node setup` may set a role again only while the stage is `preparing` or
  `node-started` and no seed phrase is stored yet.
- **Why.** The Safe's keys and address follow from the role.
- **Enforced by.** `role.require_role_can_be_set`.
- **Checked by.** `tests/test_node_setup.py`.

### SETUP-4

**The validator type decides the Safe's workchain.**

- **Rule.** A solo or nominator validator's Safe is in the masterchain, `-1`. So are a
  nominator's contract and owner. A DePool validator's Safe is in the workchain of its `NODE_WC`
  setting, and its DePool is in workchain 0.
- **Why.** The elector accepts a stake only from the masterchain, and the nominator contract
  refuses any other.
- **Enforced by.** `wallets.wallet_workchain`, `role.ask_role`, `nominator.hot_wallet_address`,
  `DePool-prep.sh`, and the election scripts' own check of the Safe's address.
- **Checked by.** `tests/test_solo_validation.py`.

### SETUP-5

**A solo or nominator stake is a recorded number above the minimum.**

- **Rule.** A solo or nominator validator stakes a fixed number of whole tokens, recorded by
  `stake set`. The number must be at least the elector's minimum stake plus 1 token. Without a
  recorded stake no schedule is installed.
- **Why.** A mode is arithmetic done once, when the operator chooses it; a bid cannot be taken
  back. Both electors take 1 token off a stake before they compare it with the minimum.
- **Enforced by.** `stake.set_stake`, `stake.least_stake_tokens`, `stake.require_recorded_stake`.
- **Checked by.** `tests/test_solo_validation.py`.

### SETUP-6

**A schedule is installed only for a finished setup.**

- **Rule.** `election start` installs a schedule only for a node that has a role, is at stage
  `imported` or `ready-for-elections`, is synchronized, and has no schedule yet.
- **Why.** A run against an unfinished setup can only fail, every interval.
- **Enforced by.** `cli.main`, `elections.enable_node_election_schedule`.
- **Checked by.** `tests/test_node_setup.py`, `tests/test_solo_validation.py`.

## Operations from the Safe

The protocol these rules belong to is in [wallet-operations.md](wallet-operations.md).

### OP-1

**Every message lives 60 s.**

- **Rule.** Every message that an evernode command or a scheduled run signs for the Safe expires
  `MSG_LIFETIME`, 60 s, after it was built. The only exceptions are the deploy messages of the
  Safe and of the DePool, which live 600 s.
- **Why.** The lifetime is how long "it did not land" stays uncertain. A rerun of a deployment
  recognises an account that is already active, so a late deploy message does no harm.
- **Enforced by.** `--lifetime "$MSG_LIFETIME"` on every `submitTransaction` and
  `confirmTransaction` the kit and `evernode/scripts.py` build.
- **Checked by.** `tests/test_depool_operations.py`, `tests/test_signature_handshake.py`,
  `tests/test_wallet_send.py`.

### OP-2

**Success is the operation's effect on chain.**

- **Rule.** An operation has succeeded when its own effect is on chain: a condition that becomes
  true and stays true, such as a stake that grew or a key the elector lists. It must never be
  decided from the node accepting a message, from a transaction leaving the pending list, or from
  a count that other activity also moves.
- **Why.** The Safe refuses a bad message before it accepts it, which leaves nothing in any block.
  An entry also leaves the pending list by expiring.
- **Enforced by.** `Await_Effect`, `Complete_And_Await` and the `Effect_` functions in
  `managed_ops.shinc`.
- **Checked by.** `tests/test_await_effect.py`, `tests/test_election_verdict.py`.

`wallet send` takes the Safe's own payment as its effect, a balance below the one before, and
never reads the destination: a transfer that bounces back counts as sent
([wallet-operations.md](wallet-operations.md#send-to-an-address)).

### OP-3

**A reported failure is final.**

- **Rule.** An operation must report failure only when nothing it sent can still execute. Every
  wait outlasts the lifetime of every message sent before it, and a confirmation, the first for a
  key as much as one sent again, is sent only while its whole lifetime and 10 s for its block
  remain before the deadline.
- **Why.** An operation that executes after it was reported as failed gets repeated by the
  operator.
- **Enforced by.** `Signing_Window`, `Complete_And_Await`, `Sign_To_Execution`,
  `Submit_And_Identify` in `managed_ops.shinc`.
- **Checked by.** `tests/test_depool_operations.py`, `tests/test_signature_handshake.py`.

### OP-4

**A transaction is recognised by its content and its creator.**

- **Rule.** The transaction an operation submitted is the pending entry with the same destination,
  value, payload, bounce flag and send flags, created by this node's submitting key; for a fresh
  submit it must also be newer than the start of the attempt. It is never picked by its position
  in the list or by a count. More than one match is refused.
- **Why.** Every operation of every custodian targets the same few addresses, so the newest entry
  may be someone else's.
- **Enforced by.** `Find_Matching_Pending_Trans`, `Submit_And_Identify`.
- **Checked by.** `tests/test_depool_operations.py`, `tests/test_nominator.py`,
  `tests/test_election_verdict.py`.

### OP-5

**A pending transaction is finished, not doubled.**

- **Rule.** A DePool operation, the funding of the nominator deployer and the proxy top-up, when
  they find their own matching transaction pending, finish it and do not submit another.
- **Why.** A run that died between submit and signature left a real transaction with one
  signature. Each new submit adds another, and five fill the queue.
- **Enforced by.** The files in `depool_functions/`, `scripts.NOMINATOR_FUND_DEPLOYER`,
  `top_app_account` in `functions.shinc`.
- **Checked by.** `tests/test_depool_operations.py`, `tests/test_nominator.py`,
  `tests/test_proxy_top_up.py`.

A scheduled run finishes a proxy top-up it finds. It does not finish a pending bid, tick,
replenishment or recovery: it stops at it ([ELECT-6](#elect-6)). `stake recover` and
`wallet send` submit a new request each time they run.

### OP-6

**Signatures go one at a time, inside the transaction's life.**

- **Rule.** Signatures are added one at a time. Each key is chosen from the wallet's own
  confirmation mask, and the next is sent only after the wallet shows the one before. Signing
  never goes past the transaction's expiry. A confirmation the wallet has not recorded within its
  lifetime is sent again, three times at most.
- **Why.** Sending every signature and reading once raced the block that carried them. Inside the
  transaction's life, an entry that leaves the list has executed; outside it, it may have expired.
- **Enforced by.** `Sign_To_Execution`, `Unsigned_Local_Keys`, `Await_Signature_Recorded`,
  `Trans_Expires_At`.
- **Checked by.** `tests/test_signature_handshake.py`, `tests/test_depool_operations.py`.

### OP-7

**No submit into a full queue.**

- **Rule.** With a Safe that needs more than one signature, the election scripts, the DePool
  operations, the deployer funding and `wallet send` must not submit while the submitting key has
  5 live transactions in the wallet.
- **Why.** The Safe refuses the sixth before it accepts the message, so nothing on chain says why.
- **Enforced by.** `Require_Queue_Space` in `managed_ops.shinc`, called by each of them.
- **Checked by.** `tests/test_queue_space.py`.

The proxy top-up and `stake recover` do not make this check. A DePool operation finishes its own
waiting entry, if there is one, before this check applies, so a full queue never stops it
(`tests/test_depool_operations.py`).

### OP-8

**A read that failed is not an answer.**

- **Rule.** In the kit, a failed read must stop the step that needed it, or be read again. It
  must never be taken for a value:
  - an account that could not be read is not an account with balance 0;
  - a custodian count that is not a positive number is not "one signature";
  - an empty participant list is not "this node is not in it";
  - a pending list that could not be read is not "nothing pending".
- **Why.** Each of these once led a run to send something it should not have.
- **Enforced by.** `Get_Account_Info`, `Get_Account_Custodians_Info`, `Elector_ADNL_Search` in
  `functions.shinc`; `Sign_To_Execution`, `Submit_And_Identify` in `managed_ops.shinc`; the checks
  after each read in `prepare_elections.sh` and `take_part_in_elections.sh`.
- **Checked by.** `tests/test_account_reads.py`, `tests/test_election_verdict.py`,
  `tests/test_prepare_elections.py`, `tests/test_depool_operations.py`.

### OP-9

**Only a named transaction is confirmed by hand.**

- **Rule.** `depool confirm` and `stake confirm` sign exactly the transaction id the operator
  names. An id the Safe does not hold is refused, and nothing is sent. Before asking, the command
  shows what the transaction sends, to whom and with how many signatures. A transaction that pays
  none of the node's own contracts (the elector, the config contract, its DePool and the DePool's
  proxies, its nominator contract) is refused unless `--force`.
- **Why.** Signing executes the transfer the transaction carries. Every custodian key is on the
  server, so one wrong id would send that transfer wherever it points.
- **Enforced by.** `setup_actions._review_transaction`, `wallets.require_confirmable`,
  `wallets.confirm_wallet_transaction`, `Sign_Trans.sh`.
- **Checked by.** `tests/test_depool_commands.py`, `tests/test_signature_handshake.py`.

## Elections

The run these rules belong to is in [elections.md](elections.md).

### ELECT-1

**One request per election.**

- **Rule.** A node makes one elector request per election. Its file, `ID_query.boc`, is reused by
  every later run in that election.
- **Why.** The console makes a new validator key for each request, and the node keeps only the
  newest key for an election. A bid registered under an older key would hold a seat the node
  cannot validate in.
- **Enforced by.** `take_part_in_elections.sh`.
- **Checked by.** `tests/test_elector_abi.py`, `tests/test_nominator.py`.

### ELECT-2

**A registered bid is never sent again.**

- **Rule.** A run sends a bid only after it has read the elector's participant list and not found
  this election's key in it. A list that cannot be read ends the run. Once a run has seen the node
  in the list it writes `ID_elector-confirmed-bid.txt`, and no later run asks or bids in that
  election. One exception: with an elector that no bundled interface description decodes, the run
  bids if it has no request on file and never bids again once it has one.
- **Why.** Both electors add a second stake from the same wallet and key to the first.
- **Enforced by.** `take_part_in_elections.sh`, `Elector_ADNL_Search`, `Resolve_Elector_ABI`.
- **Checked by.** `tests/test_elector_abi.py`, `tests/test_election_verdict.py`.

### ELECT-3

**A bid counts when the elector lists the node.**

- **Rule.** A bid is confirmed only when the elector lists the node's ADNL address for this
  election. With an elector that cannot be read, the bid is reported as not confirmed; for a solo
  or nominator validator a stake still in the staking account is reported as a failure.
- **Why.** A signed Safe transaction is not a registered bid: the DePool, the contract or the
  elector can still refuse it.
- **Enforced by.** `Effect_Elector_Has_Our_ADNL`, `Get_Engine_ADNL_For_Election`,
  `Effect_Account_Balance_At_Most`, `take_part_in_elections.sh`.
- **Checked by.** `tests/test_election_verdict.py`, `tests/test_elector_abi.py`.

### ELECT-4

**Retries are bounded.**

- **Rule.** A bid that was sent and is not listed within two message lifetimes is sent again by
  the next scheduled run, with the same request. After `BID_ATTEMPTS_MAX`, 3, such bids the run
  writes `ID_bid-stopped.txt`, and later runs in that election do not bid. The run also stops at
  once when the DePool holds no stake in the round being elected. A run that ended before it sent
  a bid is not counted.
- **Why.** A retry repairs a lost message. A bid that can never register would otherwise cost gas
  and raise an alert at every run until the election closes.
- **Enforced by.** `Record_Bid_Attempt`, `Bid_Attempts`, `Stop_Bidding`, `Bidding_Stopped` in
  `managed_ops.shinc`; `take_part_in_elections.sh`.
- **Checked by.** `tests/test_bid_retries.py`.

### ELECT-5

**A failed preparation stops a DePool run, not a solo run.**

- **Rule.** In a DePool validator's run a preparation that fails ends the run without a bid. In a
  solo or nominator validator's run the bid step runs whatever preparation returned.
- **Why.** DePool preparation moves the DePool into the election, which the bid needs. A solo
  recovery and a solo bid do not depend on each other, and the bid cannot be made later.
- **Enforced by.** `elections._depool_sequence_program`, `elections._solo_sequence_program`.
- **Checked by.** `tests/test_solo_validation.py`.

### ELECT-6

**No bid beside a pending transaction.**

- **Rule.** A scheduled run must not bid while the Safe holds a pending transaction to the run's
  destination: the elector (solo), the nominator contract (nominator), the DePool or one of its
  proxies (DePool validator). Preparation stops the same way, except at a transaction to a proxy,
  which is a top-up it finishes. The exit code is 3 for solo and nominator and 4 for a DePool
  validator. The transaction clears when `depool confirm` or `stake confirm` finishes it, or when
  it expires an hour after it was made.
- **Why.** A second transfer beside a half-signed one would send both once the first is signed.
- **Enforced by.** `take_part_in_elections.sh`, `prepare_elections.sh`.
- **Checked by.** `tests/test_election_verdict.py`, `tests/test_nominator.py`.

### ELECT-7

**A proxy is funded before it can freeze.**

- **Rule.** A proxy whose balance, less its storage owed, is under 1.92 tokens is topped up before
  each tick, and before `election start`, `depool stake` and `depool stake-initial`. Storage owed
  is counted at the storage prices in force, and never as less than 0.1 token a day since the
  proxy's last transaction; as none when that time is not known. The top-up is 5 tokens plus the
  storage owed in whole tokens, in a transfer that does not bounce, and only to a proxy that reads
  as Active; a proxy that owes more than 50 tokens gets nothing. It is done when the proxy's
  balance is at least the old one plus 4 tokens. A top-up that fails while the proxy owes more
  than it holds stops the run before the tick, or the command; otherwise it is a warning.
- **Why.** A frozen proxy can neither carry a stake to the elector nor bring one back. Its balance
  is as of its last transaction, so a proxy idle for weeks can owe more than it shows, and the
  next small message then freezes it. A transfer that bounces is credited only after the proxy
  pays its storage, so a proxy that owes more than it holds freezes before the tokens arrive.
- **Enforced by.** The proxy loop in `prepare_elections.sh` and `wallets.fund_proxies`;
  `Proxy_Storage_Owed` and `top_app_account` in `functions.shinc`.
- **Checked by.** `tests/test_prepare_elections.py`, `tests/test_proxy_top_up.py`,
  `tests/test_depool_commands.py`.

A transfer shorter than the debt would be credited and then taken in full, freezing the proxy, so
above 50 tokens nothing is sent and the error names what the proxy needs. Whole tokens keep the
amount the same from one run to the next, which is how a run finds the top-up an earlier run left
pending ([OP-5](#op-5)). Under 1 token (`MIN_PROXY_BALANCE` in the DePool's code) a proxy refuses
the DePool's request, and the DePool rolls it back; only owing more than it holds freezes it, so
only that stops the run. A proxy returns what it holds above 2 tokens to the validator's wallet on
request (`withdrawExcessTons`).

### ELECT-8

**No stake moves to or from the elector while a proxy is not active.**

- **Rule.** While one of the DePool's proxies is frozen, or does not read as Active, DePool
  preparation ends with status 1 before the tick, and evernode refuses `election start`,
  `depool stake-initial`, `depool stake`, and `depool confirm` of a transaction to the DePool,
  with or without `--force`. Both proxies are read before either is judged. Each frozen one, and
  each one read without error but without code, is named in an alarm, in red on a terminal;
  preparation also sends an alert, and `election status` shows the latest run's alarm again.
  `depool withdraw` and `depool replenish` go ahead.
- **Why.** The stop is for stakes that travel between the DePool and the elector: the elector
  keeps what bounces off a frozen proxy, so a stake it sends back to one is lost, and a proxy
  that cannot be read may be frozen. A withdrawal only records a request in the DePool, which
  pays it from its own rounds, and no message passes a proxy; it is paid when its round
  completes, which needs the ticks to run again, so nothing arrives until the proxy is restored.
- **Enforced by.** The proxy loop in `prepare_elections.sh`; `wallets.require_live_proxies` for
  the commands, through `wallets.fund_proxies` where a top-up follows; the proxy check in
  `stake_to_depool.sh`, which a withdrawal skips; `elections.last_run` for `election status`.
- **Checked by.** `tests/test_prepare_elections.py`, `tests/test_depool_commands.py`,
  `tests/test_depool_operations.py`.

The DePool sends to the elector through a proxy on a tick (`ticktock`) and on a bid
(`participateInElections`). Preparation sends the tick, a bid follows only a preparation that
succeeded ([ELECT-5](#elect-5)), and `depool confirm` could otherwise finish either one left
pending. evernode does not restore a frozen proxy: that needs the state it had when it froze, its
code and data, sent to it with the tokens it owes. A proxy without code was deleted by the chain,
or never deployed, and cannot be rebuilt: only the DePool's constructor deploys its proxies. Right
after `depool deploy`, one still on its way reads as not yet Active, without the alarm.

What "lost" means, by the contracts in the kit: `Elector.sol` writes a payment that bounces back
as the proxy's credit (`onBounce`), and only the proxy can claim it. The proxy asks only on the
DePool's order (`recover_stake` in `DePoolProxy.sol`), and the DePool asks once: `updateRound2` in
`DePool.sol` sends no second request for a round that waits for its reward. That round never
completes, and the rounds stop rotating. When the proxy is already frozen as the DePool asks, the
request itself bounces: the DePool steps the round back (`onBounce`) and asks again on a later
tick, and nothing reaches the elector.

### ELECT-9

**No recovery or bid while the contract's hot wallet is not this Safe.**

- **Rule.** Before a nominator validator's run builds a recovery or a bid, it reads the contract's
  state and compares the hot wallet it names with the node's Safe. Unless they match, nothing is
  sent and the step ends with status 1: preparation before the recovery request, the bid before
  the console makes a key for the election. A different wallet raises an alert, and so does a
  missing tools image, with the command that rebuilds it: no later run changes either. The next
  run checks again.
- **Why.** The owner may replace the hot wallet at any time (`nominator rotate`). The contract then
  keeps what the old Safe sends without acting on it, and nothing bounces. A recovery goes out on
  every run while the elector owes the contract, so the Safe would drain into the contract, and
  the bid would only report that it did not register.
- **Enforced by.** `Nominator_Hot_Wallet_Check` in `functions.shinc`, called by
  `prepare_elections.sh` and `take_part_in_elections.sh`; the state is read as
  `nominator verify` reads it.
- **Checked by.** `tests/test_nominator.py`.

## Votes

The feature these rules belong to is in [voting.md](voting.md).

### VOTE-1

**Managed nodes vote only through `vote join`.**

- **Rule.** With `-n` or `--all`, `vote cast`, `vote run` and `vote burn` are refused.
- **Why.** `join` votes in every round from cron and records each vote; the others do neither.
- **Enforced by.** `voting.handle_vote`, `voting.managed_vote_refusal`.
- **Checked by.** `tests/test_voting.py`.

### VOTE-2

**A payment is asked about first.**

- **Rule.** Money for a proposal leaves a wallet only through `vote propose`, which shows the
  price and asks before it pays unless `--yes` is given. `--pay-from-safe`, `--pay-from` and
  `--from` are refused with every other vote command. A node's Safe pays only if it is in the
  masterchain, under the node lock, with the election lock taken after the answer.
- **Why.** A proposal's price is not returned.
- **Enforced by.** `voting.handle_vote`, `voting.pay_and_propose`,
  `voting.require_masterchain_safe`; in the engine, `propose` in `vote/src/campaign.rs`.
- **Checked by.** `tests/test_voting.py`, `tests/test_vote_schedule.py`.

### VOTE-3

**One vote pass at a time.**

- **Rule.** At most one of `vote join`, `vote leave`, `vote tick` and `vote status`'s read of the
  joined proposals works on a state directory at a time. A tick that finds the vote lock held ends
  without output; `join` and `leave` refuse; `status` shows what was last read. A pass that runs
  600 s is killed.
- **Why.** Two passes would send the same votes and write the same store.
- **Enforced by.** `VoteStore.locked`, `vote_schedule.tick`, `vote_schedule.check_open`,
  `vote_schedule.run_pass`.
- **Checked by.** `tests/test_vote_schedule.py`.

### VOTE-4

**A counted vote is recorded at once.**

- **Rule.** A vote is written to the vote store the moment the engine reports it counted, before
  the pass ends.
- **Why.** A pass killed after a vote must not lose the record of it.
- **Enforced by.** `vote_schedule.run_due_pass`, `vote_schedule.run_pass`.
- **Checked by.** `tests/test_vote_schedule.py`.

### VOTE-5

**The engine's version is pinned.**

- **Rule.** evernode runs only the `evernode-vote` version named in `voting.ENGINE_VERSION`. Any
  other is refused, with the command that installs the right one.
- **Why.** The description evernode writes and the report it reads change with the engine.
- **Enforced by.** `voting.find_engine`.
- **Checked by.** `tests/test_voting.py`.

## What is not guaranteed

These are facts of the current code, not rules. A change may remove any of them.

- A scheduled run does not finish a half-signed bid, tick, replenishment or recovery it finds. It
  stops until the transaction is confirmed by hand or expires ([ELECT-6](#elect-6)).
- evernode sets up only Safes whose every custodian key is on the server: `wallet recover` asks
  for every phrase. The kit still handles a Safe with fewer keys here, by stopping and saying which
  transaction needs signing elsewhere, but no setup command leads to that state.
- With an elector whose participant list cannot be decoded, a bid's acceptance is not confirmed
  ([ELECT-3](#elect-3)).
- `election-cron.log`, `transactions.log` and the election directories grow without limit.
  Nothing rotates or removes them.
- A value edited by hand in a node's `env.sh` is lost at the next `node sync-scripts`
  ([state.md](state.md#scriptsenvsh)).
- The host's firewall is not managed.
