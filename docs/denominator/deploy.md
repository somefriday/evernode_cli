# evernode denominator deploy

Create a denominator validator's pools on chain.

## Example

```bash
sudo evernode denominator deploy -n validator01
```

## What it does

For each pool not on chain yet, pays its one-shot deployer 13 tokens from the Safe and sends the deployer's trigger through the node's console. The deployer creates the pool with everything it holds, about 12.4 tokens after fees, and deletes itself. It waits until each pool is active.

## Flags

| Flag           | Value | Default  | Meaning                       |
| -------------- | ----- | -------- | ----------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.             |
| `--yes`        |       | ask      | Skip the confirmation prompt. |

## Behaviour

- After `denominator prepare`, on a synchronized node.
- A pool already on chain is left as it is. After a failure, run it again: a deployer that is paid is not paid twice.
- With several Safe signatures the payment is proposed, then approved: do not interrupt it.
- Each pool keeps 10 tokens for its storage for life; no command withdraws them.
- Prints the next command: `denominator verify`.

## See also

- [denominator prepare](prepare.md)
- [denominator verify](verify.md)
