# Glossary

The words this spec uses and what each means here. A word that names a file or a field is
defined in [state.md](state.md).

| Word                  | Meaning                                                                                 |
| --------------------- | --------------------------------------------------------------------------------------- |
| ADNL address          | The network address a validator key is known by. The elector lists a bid under it.      |
| bid                   | A node's request to the elector to take part in one election, with a stake.             |
| console               | ever-node's control tool, run inside the node container. evernode's only way to a node's chain data. |
| custodian             | One key holder of a Safe. A Safe has 1 to 31.                                            |
| deployer              | The one-shot contract that creates a nominator contract and deletes itself.             |
| DePool                | A staking contract that holds the stakes of many participants and bids for one validator. |
| description           | What evernode writes into the vote engine's pipe: the endpoint, key rings and the payer. |
| effect                | The change on chain that proves an operation worked.                                    |
| election              | The time in which the elector takes bids for one round.                                 |
| election id           | The Unix time at which the elected validator set starts. Names an election.             |
| election lock         | A node's lock that every user of its Safe holds.                                        |
| elector               | The masterchain contract that takes bids, holds stakes and names the next validator set. |
| hot wallet            | A nominator validator's Safe: it may tell the contract to bid and to recover.            |
| joined proposal       | A config proposal this host votes for in every round until it is decided.               |
| key ring              | The validator keys in a node's `node_cfg/config.json`, one per election.                 |
| kit                   | The election scripts, configs and contract files evernode copies into each node.        |
| managed image         | A node image that has a record in `images/`.                                             |
| marker                | A file in an election's directory that later runs stop at.                               |
| masterchain           | Workchain -1. The elector and the config contract live there.                            |
| nanotoken             | One billionth of a token. Contracts count in nanotokens.                                 |
| node lock             | The lock one evernode command holds on one node.                                         |
| node record           | `node.json`: everything evernode knows about a node.                                     |
| nominator contract    | A single-nominator contract: it holds one validator's stake for a cold owner.           |
| owner                 | The wallet that may withdraw from a nominator contract. Its key is never on the server.  |
| pass                  | One run of the vote engine for one joined proposal.                                      |
| pending transaction   | A transfer a Safe holds until enough custodians have signed it.                          |
| phase                 | How far a node's containers have come: `preparing`, `prepared`, `console-ready`, `removed`. |
| proxy                 | One of a DePool's two helper contracts in the masterchain. The DePool bids through one of them, by turns. |
| registry lock         | The lock for short checks across all nodes.                                              |
| role                  | A node's validator type and wallet profile, set by `node setup`.                         |
| round                 | The time one validator set works. Also one of a DePool's four stake buckets.             |
| run                   | One start of a node's election sequence by cron.                                         |
| runner                | `run-elections.sh`: what cron starts. It takes the election lock and starts the sequence. |
| Safe                  | A node's SafeMultisigWallet. It pays for and signs everything the node sends.            |
| seed phrase           | The 12 words a key pair is derived from.                                                 |
| sequence              | The script a run follows: preparation, then the bid.                                     |
| setup stage           | How far a node's validator setup has come.                                               |
| stake fee             | The value a DePool asks with each instruction and partly returns.                        |
| staking account       | The account whose balance a solo or nominator validator stakes: the Safe, or the contract. |
| state directory       | Where evernode keeps everything it owns. `/var/lib/ever-validator` by default.           |
| synchronized          | A node at the head of the chain: both lags within the limit.                             |
| tick                  | The message that makes a DePool move its rounds and enter the open election. `vote tick` is something else: cron's start of the vote passes. |
| token                 | The network's coin, EVER on mainnet.                                                     |
| tools image           | The Docker image that compiles the nominator contract and builds its messages.           |
| validator set         | The validators of one round. Config param 34 is the current set, 36 the next.            |
| validator type        | How a node stakes: `depool`, `solo` or `nominator`.                                      |
| vote engine           | `evernode-vote`, the program that checks, pays for and casts config votes.               |
| vote lock             | The lock one `vote join`, `vote leave` or `vote tick` holds on a state directory.        |
| wallet mode           | `new`: evernode generates the Safe. `import`: the Safe exists.                           |
