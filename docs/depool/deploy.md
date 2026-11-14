# evernode depool deploy

Deploy the funded DePool.

## Example

```bash
sudo evernode depool deploy -n validator01
```

## What it does

Deploys the DePool with the settings chosen at `node create`, waits until it is active and checks that it names this node's Safe as its validator wallet.

## Flags

| Flag           | Value | Default  | Meaning                       |
| -------------- | ----- | -------- | ----------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.             |
| `--yes`        |       | ask      | Skip the confirmation prompt. |

## Behaviour

- Needs stage `depool-prepared` and a balance of twice the balance threshold plus 5 tokens (45 by default).
- On a brand-new network, deploy only after the first validator-set change: the constructor needs config param 32.
- An active account whose constructor failed is reported as such and the stage stays `depool-prepared`.

## See also

- [depool stake-initial](stake-initial.md)
