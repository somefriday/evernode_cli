# evernode denominator withdraw

Take the validator's own funds out of an idle pool, to the Safe.

## Example

```bash
sudo evernode denominator withdraw -n validator01 --pool A --amount 500
```

## What it does

Sends the pool the validator withdrawal operation from the Safe, with 1 token for its gas. The pool pays the amount to the Safe.

## Flags

| Flag           | Value   | Default  | Meaning                       |
| -------------- | ------- | -------- | ----------------------------- |
| `-n`, `--name` | NAME    | required | The managed node.             |
| `--amount`     | TOKENS  | required | Whole tokens to withdraw.     |
| `--pool`       | `A` `B` | required | The pool to withdraw from.    |
| `--yes`        |         | ask      | Skip the confirmation prompt. |

## Behaviour

- Only while the pool is idle, between its rounds: a stake at the elector cannot be touched.
- Takes at most what the pool holds beyond its stakers' funds and its 10-token reserve; stakers' money never leaves.
- Below the minimum validator funds the pool stops bidding: withdraw everything only when retiring it.
- The pool lowers the validator's funds by the whole amount, to no less than 0, even where part of it was a refused deposit. Returning one costs the validator that much of its recorded funds: deposit again before the pool bids.
- Holds the node's election lock: a scheduled run waits.

## See also

- [denominator deposit](deposit.md)
- [denominator retire](retire.md)
