# evernode depool stake

Add an ordinary stake to the DePool from the Safe.

## Example

```bash
sudo evernode depool stake -n validator01 --amount 50000
```

## What it does

Sends the stake, signs it with the local custodian keys and waits until the DePool shows it.

## Flags

| Flag           | Value  | Default  | Meaning                       |
| -------------- | ------ | -------- | ----------------------------- |
| `-n`, `--name` | NAME   | required | The managed node.             |
| `--amount`     | TOKENS | required | Whole tokens to stake.        |
| `--yes`        |        | ask      | Skip the confirmation prompt. |

## Behaviour

- The stake joins the round that is collecting now, and stays locked until that round completes.
- The Safe must hold the amount plus fees.
- Refused while a scheduled election run holds the node's lock; try again when it finishes.

## See also

- [depool info](info.md)
