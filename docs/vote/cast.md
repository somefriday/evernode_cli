# evernode vote cast

Vote with validator keys named by file.

## Example

```bash
evernode vote cast --keyring keyring.json --proposal HASH --watch
```

## What it does

Votes for a registered proposal with every current validator key the named files hold, and follows each vote until the config counts it.

## Flags

| Flag         | Value   | Default              | Meaning                                                                   |
| ------------ | ------- | -------------------- | ------------------------------------------------------------------------- |
| `--proposal` | HASH    |                      | The registered proposal.                                                  |
| `--keyring`  | FILE    |                      | A node's `config.json` or a copy of its `validator_key_ring`; repeatable. |
| `--control`  | FILE    |                      | A node's `console.json`: the node signs; repeatable.                      |
| `--relay`    | FILE    | external messages    | Masterchain wallet file that carries the votes as internal messages.      |
| `--watch`    |         | off                  | Keep voting in every round until the proposal is gone.                    |
| `--poll`     | SECONDS | `15`                 | Time between checks.                                                      |
| `--settle`   | SECONDS | `40`                 | Wait after a new validator set before voting.                             |
| `--timeout`  | SECONDS | none                 | Give up after this long.                                                  |
| `--endpoint` | URL     | `$EVERNODE_ENDPOINT` | GraphQL endpoint of the network.                                          |

## Behaviour

- For machines without a managed node; needs no root, and records nothing.
- With `-n` or `--all` it is refused: managed nodes vote through `vote join`.
- Any ever-node's key ring is copied by `(umask 077; sudo jq '{validator_key_ring}' /PATH/TO/node_cfg/config.json > keyring.json)`.
- After the next election a copy holds no current key, and `vote status --keyring keyring.json --keys` shows `holds []`: copy it again. `--watch` reads it again at every check.
- A `--keyring`, `--control` or `--relay` file must be readable by its owner only.
- Without `--watch` it ends once every key is counted in this round, with an error if a key file could not be read.

## See also

- [vote join](join.md)
