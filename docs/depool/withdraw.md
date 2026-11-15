# evernode depool withdraw

Withdraw this node's stake from the DePool, or cancel a withdrawal.

## Example

```bash
sudo evernode depool withdraw -n validator01 --amount 1000
sudo evernode depool withdraw -n validator01 --cancel
```

## What it does

Records the request in the DePool and waits until the contract shows it.

## Flags

| Flag           | Value  | Default  | Meaning                                              |
| -------------- | ------ | -------- | ---------------------------------------------------- |
| `-n`, `--name` | NAME   | required | The managed node.                                    |
| `--amount`     | TOKENS |          | Withdraw this much when its round completes.         |
| `--all`        |        |          | Withdraw everything: stops reinvestment.             |
| `--cancel`     |        |          | Drop the pending withdrawal and resume reinvestment. |
| `--yes`        |        | ask      | Skip the confirmation prompt.                        |

## Behaviour

- Exactly one of `--amount`, `--all`, `--cancel`.
- Nothing is paid at once: funds return to the Safe as each round completes, which needs the election schedule to keep running.
- Withdrawing the stake behind the validator assurance ends validation.
- Goes ahead while a proxy of the DePool is frozen: the request is recorded, but nothing arrives until the proxy is restored and the schedule ticks again.

## See also

- [depool info](info.md)
