# evernode nominator deploy

Deploy the nominator contract.

## Example

```bash
sudo evernode nominator deploy -n validator01
```

## What it does

Funds a one-shot deployer from the Safe (11 tokens), triggers it and waits until the contract is active.

## Flags

| Flag           | Value | Default  | Meaning                       |
| -------------- | ----- | -------- | ----------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.             |
| `--yes`        |       | ask      | Skip the confirmation prompt. |

## Behaviour

- The Safe needs about 12 tokens. It is signed like every other Safe transaction, with all local custodian keys.
- A Safe that needs several signatures proposes the payment, then approves it: the command says when not to interrupt (Ctrl+C) and when the payment is done. Cut off in between, wait a minute and run the command again: it finishes the same payment instead of proposing another.
- Refuses if the Safe is not the one the contract was prepared for.
- While the contract does not appear, the deployer's trigger is sent again: three sends, a minute apart.
- Safe to run again: an active contract is reported as already deployed, and a funded deployer is not funded twice.

## See also

- [nominator verify](verify.md)
