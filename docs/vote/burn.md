# evernode vote burn

Vote for the master-key burn in every round.

## Example

```bash
evernode vote burn --keyring keyring.json
```

## What it does

The same as `vote run --burn`.

## Flags

| Flag         | Value   | Default                   | Meaning                                                                   |
| ------------ | ------- | ------------------------- | ------------------------------------------------------------------------- |
| `--label`    | TEXT    | `it is a fight club baby` | Public text the burn key is derived from.                                 |
| `--keyring`  | FILE    |                           | A node's `config.json` or a copy of its `validator_key_ring`; repeatable. |
| `--control`  | FILE    |                           | A node's `console.json`: the node signs; repeatable.                      |
| `--relay`    | FILE    | external messages         | Masterchain wallet file that carries the votes as internal messages.      |
| `--poll`     | SECONDS | `15`                      | Time between checks.                                                      |
| `--settle`   | SECONDS | `40`                      | Wait after a new validator set before voting.                             |
| `--timeout`  | SECONDS | none                      | Give up after this long.                                                  |
| `--endpoint` | URL     | mainnet's public          | GraphQL endpoint of the network.                                          |

## Behaviour

- The burn proposal must be registered already, with the same label.
- With `-n` or `--all` it is refused: managed nodes vote through `vote join`.
- A `--keyring`, `--control` or `--relay` file must be readable by its owner only.

## See also

- [vote burn-key](burn-key.md)
- [vote run](run.md)
