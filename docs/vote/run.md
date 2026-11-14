# evernode vote run

Vote for a change, named by what it does, in every round.

## Example

```bash
evernode vote run --keyring keyring.json --param 17 --value-json "$(cat p17.json)"
```

## What it does

Like `vote cast --watch`, but takes the change itself instead of its hash and stops when it is in effect.

## Flags

| Flag                 | Value                        | Default                   | Meaning                                                                   |
| -------------------- | ---------------------------- | ------------------------- | ------------------------------------------------------------------------- |
| `--burn`             |                              |                           | Change: replace the master key with an unusable one.                      |
| `--label`            | TEXT                         | `it is a fight club baby` | Public text the burn key is derived from.                                 |
| `--dictator`         | KEY                          |                           | Change: appoint this master key (hex).                                    |
| `--allow-usable-key` |                              | off                       | Allow `--dictator` with an ordinary key.                                  |
| `--param`            | N                            |                           | Change: set config param N.                                               |
| `--value-json`       | JSON                         |                           | The param's value as `param` prints it.                                   |
| `--value-boc`        | BOC                          |                           | The param's value as base64 or `@file`.                                   |
| `--config-code`      | BOC                          |                           | Change: replace the config contract's code.                               |
| `--elector-code`     | BOC                          |                           | Change: replace the elector's code.                                       |
| `--elector-data`     | BOC                          | empty cell                | Data for the elector's upgrade hook.                                      |
| `--if-hash`          | `auto` `current` `none` HASH | `auto`                    | Apply only if the param still has this value.                             |
| `--force`            |                              | off                       | Allow changes to params 0, 1, 8-12, validator sets and code.              |
| `--keyring`          | FILE                         |                           | A node's `config.json` or a copy of its `validator_key_ring`; repeatable. |
| `--control`          | FILE                         |                           | A node's `console.json`: the node signs; repeatable.                      |
| `--relay`            | FILE                         | external messages         | Masterchain wallet file that carries the votes as internal messages.      |
| `--poll`             | SECONDS                      | `15`                      | Time between checks.                                                      |
| `--settle`           | SECONDS                      | `40`                      | Wait after a new validator set before voting.                             |
| `--timeout`          | SECONDS                      | none                      | Give up after this long.                                                  |
| `--endpoint`         | URL                          | `$EVERNODE_ENDPOINT`      | GraphQL endpoint of the network.                                          |

## Behaviour

- The proposal must be registered already: register it with `vote propose`. Otherwise it stops with "nobody pays".
- With `-n` or `--all` it is refused: managed nodes vote through `vote join`.
- A `--keyring`, `--control` or `--relay` file must be readable by its owner only.

## See also

- [vote cast](cast.md)
- [vote propose](propose.md)
