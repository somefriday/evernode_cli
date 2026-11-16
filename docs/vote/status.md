# evernode vote status

Show the network's governance state and the proposals joined on this host.

## Example

```bash
sudo evernode vote status
```

## What it does

Prints the master key, the voting rules, the current validator set, when the round ends, where the elections are and every open proposal. As root it adds the proposals joined here.

## Flags

| Flag           | Value | Default              | Meaning                                                                   |
| -------------- | ----- | -------------------- | ------------------------------------------------------------------------- |
| `--endpoint`   | URL   | mainnet's public     | GraphQL endpoint of the network.                                          |
| `-n`, `--name` | NAME  |                      | Use this managed node's validator keys (root).                            |
| `--all`        |       |                      | Use every managed node's validator keys (root).                           |
| `--keyring`    | FILE  |                      | A node's `config.json` or a copy of its `validator_key_ring`; repeatable. |
| `--control`    | FILE  |                      | A node's `console.json`: the node signs; repeatable.                      |
| `--keys`       |       | off                  | Show which current validator key each named node holds.                   |

## Behaviour

- Works without root and without a node; `-n` and `--all` need root.
- Without `--endpoint` it reads mainnet through its public endpoint, and refuses a node on devnet named with `-n` or `--all`.
- A `--keyring` or `--control` file must be readable by its owner only.
- Each open joined proposal is read from the chain first, through the endpoint it was joined with and without keys: one decided since its last pass closes as `applied` or `not applied`; the others show their progress now, the last pass with its error, and the next pass. Nothing is voted. While a vote pass runs, they show what was last read.
- Warns when a proposal is joined but no cron daemon runs or the vote cron line is missing.

## See also

- [vote join](join.md)
- [vote history](history.md)
