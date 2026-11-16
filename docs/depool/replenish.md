# evernode depool replenish

Top up the DePool's own operating balance.

## Example

```bash
sudo evernode depool replenish -n validator01 --amount 10
```

## What it does

Sends tokens from the Safe to the DePool's balance, which pays for its own messages.

## Flags

| Flag           | Value  | Default  | Meaning                       |
| -------------- | ------ | -------- | ----------------------------- |
| `-n`, `--name` | NAME   | required | The managed node.             |
| `--amount`     | TOKENS | required | Whole tokens to send.         |
| `--yes`        |        | ask      | Skip the confirmation prompt. |

## Behaviour

- Not a stake: it earns nothing and cannot be withdrawn.
- When the DePool's own balance, its account less the stakes it holds, falls under 10 tokens, the DePool stops advancing its rounds.
- When the own balance is 2 tokens under the balance threshold, stake commands refuse; this one still runs.
- When the own balance is 3 tokens under the threshold, the scheduled election run tops it up from the Safe to about 7 tokens above it.
- Refuses if the Safe holds less than the amount plus 1 token for fees.
- Each run adds funds again. After an interrupted run, check `depool info` before running it again: a transfer already sent can still land within a minute.

## See also

- [depool info](info.md)
