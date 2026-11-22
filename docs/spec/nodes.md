# Host, images and nodes

How the host is prepared, how a node image is built, and the life of a node: create, start, stop,
update, copy, refresh. Flags are on each command's page in the [command reference](../README.md).

Every command here but `host check` needs root. Every command that changes a node holds that
node's lock ([LOCK-1](properties.md#lock-1)).

## Host

Commands: [`host check`](../host/check.md), [`host setup`](../host/setup.md). Code: `host.py`.

**Requires**

- `host setup`: root, and Ubuntu or Debian with a known release codename.

**Logic**

`host check` reads and changes nothing. It reports:

1. Each command of `host.REQUIRED_COMMANDS`: `docker`, `git`, `tail`, `jq`, `bc`, `flock`, `cron`,
   `chronyc`, `gawk`, `rsync`, `yq`.
2. The Docker server's version and the Compose plugin's version.
3. Whether each service of `host.REQUIRED_SERVICES` is active: `docker`, `cron`, `chrony`.
4. The vote engine: its path, or why `voting.find_engine` refuses it
   ([VOTE-5](properties.md#vote-5)).

It exits 0 only if all four hold on Linux.

`host setup` shows its plan, asks, then:

1. Installs with apt: `ca-certificates`, `curl`, `git`, `jq`, `bc`, `gawk`, `rsync`, `util-linux`,
   `procps`, `cron`, `chrony`.
2. Installs Docker Engine, Buildx and Compose from Docker's own repository, each only when it is
   missing.
3. Downloads `yq` 4.44.5 for the host's architecture to `/usr/local/bin/yq`.
4. Enables and starts `docker`, `cron` and `chrony`.
5. Builds the vote engine with Docker into `/usr/local/bin`, unless the right version is there.
6. Runs the check of `host check`. A check that still fails is an error.

**Result**

A host on which every other command can run. The firewall is not changed.

**On failure**

Each step is safe to run again. Run `host setup` again.

## Images

Commands: [`image build`](../image/build.md), [`image rebuild`](../image/rebuild.md),
[`image list`](../image/list.md), [`image remove`](../image/remove.md). Code: `images.py`.

A managed image is a Docker image with a record in `images/`
([state.md](state.md#image-records)). A node runs an image by its id
([STATE-3](properties.md#state-3)).

**Logic** of a build (`images.build_managed_image`):

1. Clone the node repository, with its submodules, and the ever-cli repository into a temporary
   directory. Check out the two refs and read the commits they resolve to.
2. Collect the inputs: both repositories and commits, the Rust version, the node's build
   features, and the SHA-256 of the Dockerfile. A rebuild adds a random value. An image repository
   other than `local/ever-node` is an input too.
3. The key is the SHA-256 of the inputs. The tag is the image repository and the first 16 digits
   of the key.
4. Take the build lock for that key. A second build of the same key is refused while one runs.
5. If a record with that key exists and this is not a rebuild, check that its image exists and
   return it. Nothing is built.
6. Build with `--pull --no-cache`. The build may take 2 h.
7. Check the result: the image must hold `ever-node`, `console`, `keygen`, `ever-cli`, `jq`, `yq`,
   `bc`, `flock`, `curl` and `getent`.
8. Save the record, under the registry lock.

The defaults are in `images.py`: ever-node `master`, ever-cli `0.44.0`, Rust `1.90.0`, feature
`statsd`.

`image rebuild` builds again from the commits of an existing record. The random value makes it a
new key, a new tag and a new record. The old image stays.

`image remove`, under the registry lock, finds the one record the name matches and refuses while
the image is in use ([STATE-3](properties.md#state-3)). Then it removes the Docker image and the
record.

**On failure**

A failed build leaves no record. Its temporary directory is removed.

## Create a node

Command: [`node create`](../node/create.md). Code: `creation.handle_node_creation`,
`provisioning.py`.

**Requires**

- A name that matches `configuration.NODE_NAME_PATTERN`, is not reserved
  ([STATE-6](properties.md#state-6)) and is not a node already.
- A memory ceiling: a whole number with `M` or `G`.
- The node's public IPv4.
- A kit: the one installed with evernode, a local directory that holds `scripts/`, or a
  repository and ref. A missing kit is refused before any question.
- An image: a managed image, or a source ref to build. Without a terminal one of the two must be
  named.

**Logic**

1. Ask for what the flags did not give. In a terminal the IP's default comes from
   `api.ipify.org`.
2. Derive the container names `evernode-NAME` and `statsd-NAME`. Refuse a name that any Docker
   container or managed node has ([STATE-5](properties.md#state-5)).
3. Choose the ports: ADNL from 58888 up, metrics from 9102 up. A port is free when no node has
   it, the host can bind it and no container's binding holds it. A port named by flag that is
   taken is refused.
4. Choose the image: the named record, a build, or the menu of source versions and local images.
5. Show the plan. `--dry-run` ends here. Ask, unless the image came from the menu.
6. Check the chosen record's tag still names its image, or build the image.
7. Under the node lock, reserve the workspace (`provisioning.provision_node_workspace`). Under the
   registry lock: the directory must not exist, the names and ports must still be free, the image
   must exist. The record is saved with phase `preparing`.
8. Prepare the workspace (`provisioning.prepare_node_workspace`):
   1. Render the kit into `.prepared-source/`: a copy of `scripts/`, `configs/` and `contracts/`,
      the node's `env.sh`, and a manifest with their fingerprints.
   2. Copy the three directories into the workspace.
   3. Make `node_cfg/`, `keys/`, `node_db/`, `elections/` and `logs/`.
   4. Write `compose.json`. Save the record with phase `prepared`.
9. Start the node, as in [Start a node](#start-a-node).

**Result**

A running node that is syncing: phase `console-ready`, setup stage `node-started`, no role. The
next steps are `node sync --wait` and `node setup` ([setup.md](setup.md)).

**On failure**

- A failure after step 7 removes what this run made: the containers, the network and the
  workspace. A directory that was there before the run is not touched.
- If that cleanup could not finish, or the process was killed, the workspace stays with phase
  `preparing`. `node create --resume -n NAME` repeats step 8 from the saved snapshot. It takes no
  other flag, and refuses a workspace that already holds identity files or a snapshot that was
  changed. Then `node start` starts the node.

## Start a node

Command: [`node start`](../node/start.md). Code: `nodes.apply_node_lifecycle_operation`,
`nodes.start_node`.

**Requires**

- A node whose preparation finished.
- The node's image id present in Docker.

**Logic**

1. A removed node gets its phase back: `prepared`, or `preparing` if its preparation never
   finished.
2. Look at both containers. One that has the name without the label is refused
   ([SEC-5](properties.md#sec-5)).
3. If the node container is not running, write `compose.json` again from this version's
   definition. A running node keeps its definition until it is stopped.
4. Start the exporter. Check, from a one-off container on the node's network, that the exporter's
   name resolves and its metrics page answers. A failure shows the exporter's last 30 log lines.
5. On the first start only, generate the node's identity:
   1. `node_cfg/` must be empty ([SEC-6](properties.md#sec-6)).
   2. Run `init_scripts/R_gen_init_configs.sh` in the one-off container `evernode-init-NAME`.
   3. Make `node_cfg/` and `keys/` private again.
   4. Check the result (`provisioning.validate_generated_node_configuration`): the address and
      port, control port 5888, the database path, the console's keys, and a global config equal to
      the kit's file for the node's network.
   5. Mark the node initialized.
6. Start the node container.
7. Ask the console for statistics, up to 12 times, 5 s apart. The first answer sets phase
   `console-ready` and moves setup stage `preparing` to `node-started`.

**Result**

Both containers run. Starting never installs an election schedule
([NODE-5](properties.md#node-5)).

**On failure**

- A failed identity check leaves the generated keys in place and the node not initialized. The
  next start refuses at step 5.1. Look at `node_cfg/` by hand.
- A console that never answers is an error. The containers keep running; read
  `node logs --component stderr`.

## Stop, restart, remove

Commands: [`node stop`](../node/stop.md), [`node restart`](../node/restart.md),
[`node remove`](../node/remove.md). Code: `nodes.apply_node_lifecycle_operation`.

**Requires**

- Without `--force`: the node is not in a validator set ([NODE-2](properties.md#node-2)), and for
  stop and remove it has no undecided bid ([NODE-3](properties.md#node-3)).
- `node restart`: no election schedule installed ([NODE-4](properties.md#node-4)), and a phase
  other than `preparing` or `removed`.

**Logic**

- `node stop`: remove the election schedule, then stop each of the two containers that runs. A
  container gets the timeout, 30 s unless named, to stop by itself.
- `node restart`: take the election lock, stop, then start as above.
- `node remove`: ask, stop as above, remove both containers and the node's Docker network. With
  `--purge-data`, empty `node_db/` and `logs/`. Set phase `removed`.

Every stop of a node, by these commands and by `node update` and `node lsync`, also removes the
node's scheduled stop and says so ([NODE-6](properties.md#node-6)).

With several nodes each one is done in turn. A node that fails is reported and the rest continue;
the exit code is 1.

**Result**

- After `node stop`: no containers running, no schedule.
- After `node remove`: no containers. The record, the keys and the configuration stay, and
  `node start` brings the node back. Duties on chain are not cancelled.

**On failure**

A cron file that does not start with `# Managed by evernode` is not removed, and the command
stops before it stops anything.

## Stop at a block

Command: [`node stop`](../node/stop.md) with `--after-block`, `--watch` or `--cancel`. Code:
`cli.handle_scheduled_stop`, `stop_schedule.py`.

A scheduled stop stops a node once it has applied masterchain block N, so that the state of block
N can be exported from the stopped node. The node's cron file, `/etc/cron.d/evernode_stop_NAME`,
is the schedule: the stop is scheduled while the file exists, and whoever removes the file ends
it ([NODE-6](properties.md#node-6)).

**Requires**

- One node: `--all` is refused. The parser takes one of the three flags at a time.
- To schedule: what `node stop` requires, unless `--force`; a console that reports a last applied
  masterchain block below N; no stop scheduled for the node, and no stop check of an earlier one
  still working; a cron daemon; `evernode` on root's `PATH`.

**Logic**

`--after-block N` (`stop_schedule.schedule`):

1. Print the plan. `--dry-run` ends here.
2. Under the node lock: make the refusals of `node stop` (`nodes.refuse_stop`) unless `--force`,
   read the console once, and check what **Requires** lists.
3. Write the record `stop-at-block.json`: N, the timeout, `--force`, and the time of this
   reading as `seen_below_at`.
4. Write the cron file: every minute, as root, `evernode --state-dir DIR node stop -n NAME
   --check`, with its output appended to `logs/validator/stop-at-block.log`.
5. Write the `scheduled` line to the log, and show the log as `--watch` does.

A stop check, which cron starts every minute (`stop_schedule.check`):

1. Take the stop check lock without waiting. If an earlier check holds it, end.
2. No cron file: end. No readable record: remove the cron file, write a `failed` line, end.
3. Read the console every 5 s for 55 s (`stop_schedule.BlockWait`):
   - the node has reached the block at the first reading whose `masterchainblocknumber` is N or
     more; keep that reading's `masterchainblocktime`;
   - it is ready when `masterchainblocktime + timediff - shards_timediff`, the creation time of
     the masterchain block the shard client is at, is that time or later;
   - a reading that fails, or holds a value that is not a whole number, changes nothing;
   - after each reading, if the cron file is gone, end;
   - a reading below N is the newest `seen_below_at`. A reading at or past N more than 600 s
     after `seen_below_at` ends the scheduled stop: remove the cron file and the record, write a
     `failed` line, and leave the node running.
4. Not ready after 55 s: save `seen_below_at` in the record, write a line with the last applied
   block and the blocks left, or the reading's error, and end.
5. Ready: write a line with `stop_in_seconds`, and wait 30 s. Every 5 s: if the cron file is
   gone, end.
6. Remove the cron file. If another caller removed it first, end.
7. Stop the node with `nodes.apply_node_lifecycle_operation`, the recorded timeout and
   `--force`. Write a `stopped` line, or a `failed` line with the error. Remove the record.

`--watch` (`stop_schedule.watch`) prints what is scheduled, the log's latest line, and each new
line. It ends when the cron file is gone and no stop check holds its lock. It takes no node lock
and changes nothing.

`--cancel` (`stop_schedule.cancel`), under the node lock, removes the cron file and the record
and writes a `cancelled` line.

**Result**

- After `--after-block`: the cron file, the record and a `scheduled` line. The node runs.
- After the stop check that stops the node: what `node stop` leaves, no cron file, no record, and
  a `stopped` line. `--after-block` and `--watch` end with exit code 0.
- After `--cancel`: no cron file, no record. The node runs.

**On failure**

- A refusal when scheduling changes nothing.
- A stop that is refused or fails at the block is not tried again: the node runs, nothing is
  scheduled, and the `failed` line names the error. `--after-block` and `--watch` end with exit
  code 1.
- Ctrl+C ends `--after-block` and `--watch` with exit code 130. The stop stays scheduled.
- While the console does not answer, each stop check writes the error and the stop stays
  scheduled.
- If no stop check runs for more than 600 s while the node passes block N, or the shard blocks
  of block N do not come within that time, the node is not stopped: nothing is scheduled, and
  the `failed` line names the block the node is at and when it was last seen below N.

## Sync state

Commands: [`node sync`](../node/sync.md), [`node status`](../node/status.md),
[`node list`](../node/list.md), [`doctor`](../doctor/doctor.md). Code: `nodes.py`.

**Logic**

- A node is *synchronized* when its console reports status `synchronization_by_blocks` or
  `synchronization_finished` and both lags, `timediff` and `shards_timediff`, are within 10 s
  either way (`nodes.is_node_synchronized`).
- The console prints a lag as an unsigned 32-bit number. `nodes.signed_lag` reads it as signed. A
  negative lag is a block stamped ahead of the server's clock, so the clock is late.
- `node sync` prints one reading and exits 0 if synchronized, 2 if not.
- `node sync --wait` reads every 10 s, or the interval named, until two readings in a row are
  synchronized. A console that refuses the connection is read again.
- A clock more than 10 s late ends `node sync` with exit code 2 in both forms. Waiting cannot fix
  it.
- `node status` reports the record's phase and stage, the next setup command, both containers'
  states, the restart count, the statistics and whether the node is synchronized.

## Update a node's image

Command: [`node update`](../node/update.md). Code: `nodes.update_node_image`,
`nodes.image_maintenance_blockers`.

**Requires**

- A managed image other than the node's, or `--rollback` and a saved rollback.
- No election schedule, and a node outside both validator sets
  ([NODE-4](properties.md#node-4)). `--dry-run` lists what blocks and exits 1 if anything does.

**Logic**

1. Show the plan and ask.
2. Take the node lock and the election lock. Check the blockers again.
3. Choose the target: the named image, or the saved rollback. For an update, save the current
   image as the rollback.
4. Under the registry lock, check the target exists and save the record with it. The record
   names the image before the node stops, so `image remove` cannot take it meanwhile.
5. Stop the node. Write `compose.json`. Start the node.
6. Read the statistics up to 12 times, 5 s apart, until two readings in a row are synchronized.
7. After a rollback, drop the saved rollback. Save the record.

**Result**

The node runs the target image with the same identity, database and keys.

**On failure**

The record names the new image and still holds the rollback. A node that did not synchronize in
step 6 keeps running the new image. `node update --rollback` returns to the old one.

## Copy a database between nodes

Command: [`node lsync`](../node/lsync.md). Code: `nodes.local_sync`,
`nodes._local_sync_preflight`.

**Requires**, checked under both nodes' locks ([LOCK-2](properties.md#lock-2),
[LOCK-4](properties.md#lock-4)):

- Two different nodes, both initialized and not removed.
- `rsync` installed.
- No election schedule on either node.
- A source that is synchronized and reports itself outside both validator sets.
- A target that is not synchronized.
- The same image id, the same network and the same active global config.
- Free space for the copy: the source's database without `catchains/`, plus 10 % or 64 MiB,
  whichever is more.

**Logic**

1. Check, show the plan and release the locks. `--dry-run` ends here. Ask.
2. From here a hang-up or a termination signal is handled like Ctrl+C.
3. Take the locks and check again.
4. Stop the target, then the source.
5. Copy the source's `node_db/`, without `catchains/`, into a staging directory in the target's
   workspace. The copy may take 24 h.
6. Start the source.
7. Rename the target's `node_db/` to a backup, the staging directory to `node_db/`, and start the
   target.
8. Write `lsync.json` and remove the backup.

**Result**

The target runs on a copy of the source's blocks. Keys, `node_cfg/`, `elections/` and logs of
both nodes are untouched. The target still has to catch up: `node sync --wait`.

**On failure**

A failure or an interrupt takes the way back:

1. If the target's database was already replaced: stop the target, set the copy aside, put the
   backup back.
2. Start each node that was stopped.
3. Remove the staging directory and the copy set aside.

A directory that cannot be removed is named in a warning and does not change the result. The
error says which node or database could not be restored.

## Refresh a node's kit copy

Command: [`node sync-scripts`](../node/sync-scripts.md). Code: `cli.handle_script_refresh`,
`provisioning.refresh_node_scripts`.

**Requires**

- The node's recorded kit source still exists: the installed kit, the local directory, or the
  repository. `--bundled` uses the installed kit instead and records the move.

**Logic**

1. Render the source into a temporary directory, with the node's `env.sh` written from the
   record ([state.md](state.md#scriptsenvsh)).
2. Compare it with the live `scripts/`, `configs/` and `contracts/`, file by file: updated,
   added, removed, unchanged.
3. With `--dry-run`, or when nothing differs, show the plan and install nothing.
4. Show the plan and ask. Then take the election lock
   ([LOCK-3](properties.md#lock-3)).
5. Copy each file that will be replaced or removed into `.script-backups/TIME/`.
6. Install each file under a temporary name and rename it. The directories themselves stay,
   because the node container mounts them. Remove the files the source no longer has.
7. Check each directory's fingerprint against the rendered source.
8. Save the record with the new source version and fingerprints.
9. If the node's election schedule is installed, write the runner and the sequence again
   ([elections.md](elections.md#the-schedule)). This step runs even when no file differed.

The render that was shown is the one installed. It is not rendered again after the answer.

**Result**

The node's kit copy equals its source ([STATE-4](properties.md#state-4)). Keys, database,
`elections/` and logs are untouched.

**On failure**

- A failure in step 6 leaves some files new and some old. The backup holds every replaced file.
  Run the command again.
- With several nodes a node that fails is reported and the rest continue; the exit code is 1.

## Other node commands

| Command                                  | What it does                                                         |
| ---------------------------------------- | -------------------------------------------------------------------- |
| [`node config`](../node/config.md)       | Prints the node record.                                              |
| [`node logs`](../node/logs.md)           | Runs `tail` or `docker logs` on one of the node's logs ([state.md](state.md#logs)). |
| [`node log-level`](../node/log-level.md) | Shows or sets the level of five log components in `node_cfg/log_cfg.yml` (`log_level.COMPONENTS`). A file not in the kit's layout is refused. The node applies the change at the file's refresh rate. |
| [`node resources`](../node/resources.md) | Prints container and disk use.                                       |
| [`doctor`](../doctor/doctor.md)          | Prints `node status`, free disk space and whether the cron file exists. |
