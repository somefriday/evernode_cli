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
- Refused while one of the DePool's proxies does not read as Active. A frozen one, or one without code (uninit or gone), gets a red alarm; a frozen one needs the state it had when it froze, its code and data, sent with the tokens it owes.
- Before staking, tops up from the Safe a proxy under 1.92 tokens once its storage is paid, as each scheduled run does. A failed top-up stops the command only while that proxy owes more than it holds.
- Each run adds funds again. After an interrupted run, check `depool info` before running it again: a transfer already sent can still land within a minute.

## See also

- [depool info](info.md)
