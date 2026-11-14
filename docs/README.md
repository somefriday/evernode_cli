# evernode command reference

One page per command. Each has an example, what the command does, its flags, and how it behaves. The rules these pages follow are in [rules.md](rules.md).

Every command except `host check` and the offline and key-file `vote` commands needs root. `--state-dir DIR` goes before the group (default `/var/lib/ever-validator`); `evernode --version` prints the version.

## Setup by validator type

`node status` names the next command at every step. The whole sequence for each type (`validator01` stands for your node's name; fund an address when the previous command prints it):

**DePool**

```bash
sudo evernode node create -n validator01
sudo evernode node sync -n validator01 --wait
sudo evernode wallet create -n validator01
sudo evernode wallet deploy -n validator01
sudo evernode depool prepare -n validator01
sudo evernode depool deploy -n validator01
sudo evernode depool stake-initial -n validator01
sudo evernode election start -n validator01
```

**Solo**

```bash
sudo evernode node create -n validator01 --validator-type solo
sudo evernode node sync -n validator01 --wait
sudo evernode wallet create -n validator01
sudo evernode wallet deploy -n validator01
sudo evernode stake set -n validator01 --mode balanced
sudo evernode election start -n validator01
```

**Nominator**

```bash
sudo evernode node create -n validator01 --validator-type nominator --owner-address -1:COLD_OWNER
sudo evernode node sync -n validator01 --wait
sudo evernode wallet create -n validator01
sudo evernode wallet deploy -n validator01
sudo evernode nominator prepare -n validator01
sudo evernode nominator deploy -n validator01
sudo evernode nominator verify -n validator01
sudo evernode stake set -n validator01 --mode balanced
sudo evernode election start -n validator01
```

**Import (existing Safe, from seed phrases)**

```bash
sudo evernode node create -n validator01 --import-wallet --wallet-address ADDRESS [--depool-address ADDRESS]
sudo evernode node sync -n validator01 --wait
sudo evernode wallet recover -n validator01
sudo evernode wallet verify -n validator01
# then, by type:
sudo evernode depool verify -n validator01                # DePool
sudo evernode stake set -n validator01 --mode balanced    # solo
sudo evernode nominator prepare -n validator01            # nominator; then nominator verify and stake set
sudo evernode election start -n validator01               # once the former node is fenced
```

A nominator's owner funds the contract after `nominator verify`. A solo or nominator stake has to be large enough to be elected: the elector seats the set with the largest total stake.

## Host

| Command                       | What it does                                                                                 |
| ----------------------------- | -------------------------------------------------------------------------------------------- |
| [`host check`](host/check.md) | Report whether this host has what evernode needs.                                            |
| [`host setup`](host/setup.md) | Install Docker, the tools the election scripts use and the vote engine, on Ubuntu or Debian. |

## Nodes

| Command                                     | What it does                                                           |
| ------------------------------------------- | ---------------------------------------------------------------------- |
| [`node create`](node/create.md)             | Create a validator node: its workspace, keys, containers and ports.    |
| [`node list`](node/list.md)                 | Show every managed node and its state.                                 |
| [`node status`](node/status.md)             | Show one node's containers, sync state and next setup step.            |
| [`node config`](node/config.md)             | Print a node's saved configuration.                                    |
| [`node start`](node/start.md)               | Start a node's containers.                                             |
| [`node stop`](node/stop.md)                 | Stop a node's containers and its election schedule.                    |
| [`node restart`](node/restart.md)           | Stop and start a node.                                                 |
| [`node remove`](node/remove.md)             | Remove a node's containers, keeping its keys and configuration.        |
| [`node logs`](node/logs.md)                 | Read one of a node's logs.                                             |
| [`node sync`](node/sync.md)                 | Report whether a node is synchronized, or wait until it is.            |
| [`node resources`](node/resources.md)       | Show CPU, memory, I/O and disk use.                                    |
| [`node lsync`](node/lsync.md)               | Seed an unsynchronized node's database from another node on this host. |
| [`node sync-scripts`](node/sync-scripts.md) | Refresh a node's saved scripts, configs and contracts from its source. |
| [`node update`](node/update.md)             | Switch a node to another managed image, or back.                       |

## Safe wallet

| Command                               | What it does                                                                            |
| ------------------------------------- | --------------------------------------------------------------------------------------- |
| [`wallet create`](wallet/create.md)   | Generate the node's Safe multisig wallet.                                               |
| [`wallet deploy`](wallet/deploy.md)   | Deploy the funded Safe.                                                                 |
| [`wallet recover`](wallet/recover.md) | Rebuild an imported Safe's keys from its seed phrases and check them against the chain. |
| [`wallet verify`](wallet/verify.md)   | Check an imported Safe's custodians and threshold again.                                |

## DePool validator

| Command                                           | What it does                                                               |
| ------------------------------------------------- | -------------------------------------------------------------------------- |
| [`depool prepare`](depool/prepare.md)             | Generate the DePool's key and address.                                     |
| [`depool deploy`](depool/deploy.md)               | Deploy the funded DePool.                                                  |
| [`depool stake-initial`](depool/stake-initial.md) | Stake the validator assurance from the Safe into the DePool.               |
| [`depool verify`](depool/verify.md)               | Check that an imported DePool belongs to this node's Safe.                 |
| [`depool info`](depool/info.md)                   | Show the DePool's rounds, this node's stake and pending Safe transactions. |
| [`depool stake`](depool/stake.md)                 | Add an ordinary stake to the DePool from the Safe.                         |
| [`depool withdraw`](depool/withdraw.md)           | Withdraw this node's stake from the DePool, or cancel a withdrawal.        |
| [`depool replenish`](depool/replenish.md)         | Top up the DePool's own operating balance.                                 |
| [`depool confirm`](depool/confirm.md)             | Finish a Safe transaction that is short of signatures.                     |

## Solo and nominator stake

| Command                             | What it does                                                       |
| ----------------------------------- | ------------------------------------------------------------------ |
| [`stake set`](stake/set.md)         | Record what a solo or nominator validator stakes in each election. |
| [`stake status`](stake/status.md)   | Show the stake, the staking account and what the elector owes.     |
| [`stake recover`](stake/recover.md) | Ask the elector to return the stake of finished rounds now.        |
| [`stake confirm`](stake/confirm.md) | Finish a Safe transaction that is short of signatures.             |

## Nominator contract

| Command                                       | What it does                                                  |
| --------------------------------------------- | ------------------------------------------------------------- |
| [`nominator prepare`](nominator/prepare.md)   | Compile the single-nominator contract and derive its address. |
| [`nominator deploy`](nominator/deploy.md)     | Deploy the nominator contract.                                |
| [`nominator verify`](nominator/verify.md)     | Check the deployed contract before funding it.                |
| [`nominator info`](nominator/info.md)         | Show where a nominator validator's money is.                  |
| [`nominator withdraw`](nominator/withdraw.md) | Prepare an owner withdrawal for signing elsewhere.            |
| [`nominator rotate`](nominator/rotate.md)     | Prepare the owner's replacement of the hot wallet.            |
| [`nominator submit`](nominator/submit.md)     | Broadcast a message the owner signed.                         |

## Elections

| Command                                 | What it does                                             |
| --------------------------------------- | -------------------------------------------------------- |
| [`election start`](election/start.md)   | Install a node's election schedule.                      |
| [`election status`](election/status.md) | Show a node's schedule and what the chain says about it. |
| [`election list`](election/list.md)     | Show the schedule state of every node.                   |
| [`election stop`](election/stop.md)     | Remove a node's election schedule.                       |

## Images

| Command                             | What it does                                           |
| ----------------------------------- | ------------------------------------------------------ |
| [`image build`](image/build.md)     | Build a node image from source.                        |
| [`image rebuild`](image/rebuild.md) | Build a new image from the commits of an existing one. |
| [`image list`](image/list.md)       | Show managed images and which node uses which.         |
| [`image remove`](image/remove.md)   | Delete a managed image that nothing uses.              |

## Doctor

| Command                      | What it does                                   |
| ---------------------------- | ---------------------------------------------- |
| [`doctor`](doctor/doctor.md) | Print one node's status with a few host facts. |

## Network config votes

| Command                                   | What it does                                                               |
| ----------------------------------------- | -------------------------------------------------------------------------- |
| [`vote status`](vote/status.md)           | Show the network's governance state and the proposals joined on this host. |
| [`vote param`](vote/param.md)             | Print a config param.                                                      |
| [`vote simulate`](vote/simulate.md)       | Play a proposal's whole life offline.                                      |
| [`vote propose`](vote/propose.md)         | Register a proposal and pay its price.                                     |
| [`vote join`](vote/join.md)               | Vote for a proposal now and in every round, from cron.                     |
| [`vote leave`](vote/leave.md)             | Stop voting for a joined proposal.                                         |
| [`vote history`](vote/history.md)         | List every proposal joined on this host and the votes sent.                |
| [`vote cast`](vote/cast.md)               | Vote with validator keys named by file.                                    |
| [`vote run`](vote/run.md)                 | Vote for a change, named by what it does, in every round.                  |
| [`vote burn`](vote/burn.md)               | Vote for the master-key burn in every round.                               |
| [`vote burn-key`](vote/burn-key.md)       | Print the key a burn installs.                                             |
| [`vote mint-wallet`](vote/mint-wallet.md) | Make a new wallet file to pay for proposals.                               |
