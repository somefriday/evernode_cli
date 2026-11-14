# evernode vote propose

Register a proposal and pay its price.

## Example

```bash
sudo -E evernode vote propose -n validator01 --pay-from-safe --burn
```

## What it does

Checks the change against the contract's rules, shows it and its price, dry-runs the registration on the contract's real code, asks, pays from the named wallet and follows the payment to the config's answer.

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
| `--pay-from-safe`    |                              |                           | Pay from the `-n` node's Safe (root).                            |
| `--pay-from`         | ADDRESS                      |                           | Pay from this masterchain wallet; its seed phrase is asked.      |
| `--from`             | FILE                         |                           | Pay from a wallet file.                                          |
| `-n`, `--name`       | NAME                         |                           | Use this managed node's validator keys (root).                   |
| `--ttl`              | SECONDS                      | the minimum               | Keep the proposal longer, for a higher price.                    |
| `--dry-run`          |                              | off                       | Stop after the checks; shows hash and price.                     |
| `--yes`              |                              | ask                       | Pay without asking.                                              |
| `--endpoint`         | URL                          | `$EVERNODE_ENDPOINT`      | GraphQL endpoint of the network.                                 |

## Behaviour

- One change flag is required, and one way to pay unless `--dry-run`.
- Under `sudo`, pass the endpoint with `sudo -E` or `--endpoint`: plain `sudo` drops the variable.
- The config takes payment only from the masterchain, so a DePool validator's Safe is refused (use `--from` or `--pay-from`); a Safe with several custodians carries at most 4 cells, a burn or a small param.
- The payer needs the price plus 3 tokens; the config keeps the price and returns the rest.
- A proposal already registered is not paid for again; a change already in effect is refused.
- Warns when the elections for the next round have closed: an unwon round costs one allowed loss.
- `--pay-from-safe` holds the node's election lock until it ends, so answer the prompt promptly.
- A wallet file, and the key files it names, must be readable by their owner only.

## See also

- [vote join](join.md)
- [vote mint-wallet](mint-wallet.md)
