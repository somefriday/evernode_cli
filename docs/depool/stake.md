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
- Before staking, tops up from the Safe a proxy that holds less than 1.92 tokens once it pays the storage it owes, as each scheduled run does before the tick: the stake reaches the elector through the proxies.
- Each run adds again. Interrupted after its transfer was sent, it can still take effect within about a minute: check `depool info` before running it again.

## See also

- [depool info](info.md)
