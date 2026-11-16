# evernode depool deploy

Deploy the funded DePool.

## Example

```bash
sudo evernode depool deploy -n validator01
```

## What it does

Deploys the DePool with the settings chosen at `node setup`, waits until it is active and checks that it names this node's Safe as its validator wallet.

## Flags

| Flag           | Value | Default  | Meaning                       |
| -------------- | ----- | -------- | ----------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.             |
| `--yes`        |       | ask      | Skip the confirmation prompt. |

## Behaviour

- Needs stage `depool-prepared` and a balance of twice the balance threshold plus 5 tokens (45 by default); below that it is refused with the balance the DePool holds.
- Storage is taken between two transfers: a second one that only makes up the difference falls short, so send 1 token more.
- On a brand-new network, deploy only after the first validator-set change: the constructor needs config param 32.
- Run again after a wait that ran out or a dropped session: a DePool already on chain is not deployed again, only checked.
- An active account whose constructor failed is reported as such and the stage stays `depool-prepared`. One that does not answer is reported with the error; run the command again to check it.

## See also

- [depool stake-initial](stake-initial.md)
