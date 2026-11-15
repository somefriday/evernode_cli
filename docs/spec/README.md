# evernode specification

What evernode is made of, the rules it keeps, and how each feature works from start to finish. It is
for people and agents who change or review the code. An operator needs the
[command reference](../README.md) instead; it also gives the command sequences.

Last checked against the code at commit `7700414` (evernode 0.3.3, vote engine 0.2.4).

## The files

| File                                         | What it answers                                                              |
| -------------------------------------------- | ---------------------------------------------------------------------------- |
| [architecture.md](architecture.md)           | Which parts exist, where each runs, how they reach the chain and each other. |
| [state.md](state.md)                         | What is stored where: the node record, settings, setup stages, files.        |
| [properties.md](properties.md)               | The rules that hold across features, each with an id.                        |
| [nodes.md](nodes.md)                         | Host, images and the life of a node: create, start, stop, update, copy.      |
| [setup.md](setup.md)                         | From a running node to a validator ready for elections, by validator type.   |
| [wallet-operations.md](wallet-operations.md) | How any transfer from the Safe is sent, signed and judged.                   |
| [staking.md](staking.md)                     | The commands that move or report stake: DePool, solo, nominator.             |
| [elections.md](elections.md)                 | The schedule and one scheduled run, step by step.                            |
| [voting.md](voting.md)                       | Network config votes: what evernode adds to the vote engine.                 |
| [glossary.md](glossary.md)                   | The words used here.                                                         |
| [rules.md](rules.md)                         | The rules these files follow.                                                |

Read `architecture.md` and `state.md` first. Then read `properties.md`, and the feature file for the
code you are about to touch.

The rules these files follow are in [rules.md](rules.md): what belongs where, the form of a
property and of a feature section, and how the spec is kept true.

## Out of scope

- The code of the on-chain contracts (Safe, DePool, elector, config, single-nominator). The spec
  states only the contract facts the logic relies on.
- Scripts of the election kit that no evernode command runs.
- The test harness under `tests/e2e/` and the local test network.
