# evernode vote simulate

Play a proposal's whole life offline.

## Example

```bash
evernode vote simulate --burn
```

## What it does

Registers the change and votes it through on a copy of the config contract's real code and state, with stand-in validators, and reports whether the contract and ever-node still work afterwards.

## Flags

| Flag                 | Value                        | Default                   | Meaning                                                          |
| -------------------- | ---------------------------- | ------------------------- | ---------------------------------------------------------------- |
| `--burn`             |                              |                           | Change: replace the master key with an unusable one.             |
| `--label`            | TEXT                         | `it is a fight club baby` | Public text the burn key is derived from.                        |
| `--dictator`         | KEY                          |                           | Change: appoint this master key (hex).                           |
| `--allow-usable-key` |                              | off                       | Allow `--dictator` with an ordinary key.                         |
| `--param`            | N                            |                           | Change: set config param N.                                      |
| `--value-json`       | JSON                         |                           | The param's value as `param` prints it.                          |
| `--value-boc`        | BOC                          |                           | The param's value as base64 or `@file`.                          |
| `--config-code`      | BOC                          |                           | Change: replace the config contract's code.                      |
| `--elector-code`     | BOC                          |                           | Change: replace the elector's code.                              |
| `--elector-data`     | BOC                          | empty cell                | Data for the elector's upgrade hook.                             |
| `--if-hash`          | `auto` `current` `none` HASH | `auto`                    | Apply only if the param still has this value.                    |
| `--force`            |                              | off                       | Allow changes to params 0, 1, 8-12, 31, validator sets and code. |
| `--validators`       | N                            | `3`                       | Stand-in validators.                                             |
| `--endpoint`         | URL                          | `$EVERNODE_ENDPOINT`      | GraphQL endpoint of the network.                                 |

## Behaviour

- Sends nothing. One change flag is required.
- For an elector upgrade it also runs the elector's upgrade hook and says whether the elector accepts the code.

## See also

- [vote propose](propose.md)
