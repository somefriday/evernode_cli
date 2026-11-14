# evernode vote join

Vote for a proposal now and in every round, from cron.

## Example

```bash
sudo -E evernode vote join -n validator01 --proposal HASH
sudo -E evernode vote join --keyring /PATH/TO/node_cfg/config.json --proposal HASH
```

## What it does

Reads the proposal from the chain, shows what it changes, asks, votes with every current validator key the named nodes hold, and installs a cron line that votes again in each round until the proposal is decided.

## Flags

| Flag           | Value | Default              | Meaning                                                                        |
| -------------- | ----- | -------------------- | ------------------------------------------------------------------------------ |
| `--proposal`   | HASH  | required             | The proposal's 64 hex digits.                                                  |
| `-n`, `--name` | NAME  |                      | Vote with this managed node's keys.                                            |
| `--all`        |       |                      | Vote with every managed node's keys.                                           |
| `--keyring`    | PATH  |                      | Another node's `config.json`, or `CONTAINER:/PATH` in a container; repeatable. |
| `--endpoint`   | URL   | `$EVERNODE_ENDPOINT` | GraphQL endpoint of the network.                                               |
| `--yes`        |       | ask                  | Join without asking.                                                           |

## Behaviour

- Needs root, and at least one of `-n`, `--all` and `--keyring`.
- Under `sudo`, pass the endpoint with `sudo -E` or `--endpoint`: plain `sudo` drops the variable.
- The endpoint is stored with the proposal (root-only) while it is open, so cron needs no environment.
- Cron runs `evernode vote tick` every minute. A pass is due 40 s after a new validator set starts, and a minute later while a vote is missing.
- Every pass reads each key ring again: after an election it votes with the node's new key.
- When a `--keyring` file is readable by other users, join refuses it: `chmod 600` it, or `docker exec CONTAINER chmod 600 /PATH` for one in a container; ever-node keeps that mode. Join only reads the mode, and passes do not check it again.
- Joining again replaces the nodes, key rings and endpoint. `--all` means the nodes that exist now, except removed ones; `-n` still takes a removed node.
- When the proposal is accepted, expires or loses too many rounds, the entry closes as `applied` or `not applied` and the cron line is removed.

## See also

- [Vote with a node evernode does not manage](../../voting_instruction.md#vote-with-a-node-evernode-does-not-manage)
- [vote status](status.md)
- [vote leave](leave.md)
- [vote history](history.md)
