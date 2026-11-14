# evernode vote leave

Stop voting for a joined proposal.

## Example

```bash
sudo evernode vote leave --proposal HASH
```

## What it does

Closes the entry as `left`; the cron line is removed when no joined proposal is open.

## Flags

| Flag         | Value | Default  | Meaning                       |
| ------------ | ----- | -------- | ----------------------------- |
| `--proposal` | HASH  | required | The proposal's 64 hex digits. |

## Behaviour

- Needs root; needs no endpoint.
- Votes already counted stay counted in their round.
- Fails with "a vote pass is running" while cron's pass holds the store; try again in a minute.

## See also

- [vote join](join.md)
