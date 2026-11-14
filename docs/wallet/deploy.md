# evernode wallet deploy

Deploy the funded Safe.

## Example

```bash
sudo evernode wallet deploy -n validator01
```

## What it does

Sends the Safe's deployment with the configured custodians and signature threshold, and waits until the account is active on chain.

## Flags

| Flag           | Value | Default  | Meaning                       |
| -------------- | ----- | -------- | ----------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.             |
| `--yes`        |       | ask      | Skip the confirmation prompt. |

## Behaviour

- Needs stage `wallet-created` and a funded address (about 1 token is enough for the deployment itself).
- Next step by type: `depool prepare`, `stake set`, or `nominator prepare`.

## See also

- [wallet create](create.md)
