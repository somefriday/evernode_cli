# evernode nominator verify

Check the deployed contract before funding it.

## Example

```bash
sudo evernode nominator verify -n validator01 --yes
```

## What it does

Reads the account from the chain and compares its code hash, owner and hot wallet with the prepared manifest.

## Flags

| Flag           | Value | Default  | Meaning                       |
| -------------- | ----- | -------- | ----------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.             |
| `--yes`        |       | ask      | Skip the confirmation prompt. |

## Behaviour

- Run it before the owner sends the principal.
- Ends at stage `nominator-verified`; next, the owner funds the contract and you run `stake set`.
- On an imported node it confirms that the recovered Safe derives the existing contract.

## See also

- [stake set](../stake/set.md)
