# evernode depool prepare

Generate the DePool's key and address.

## Example

```bash
sudo evernode depool prepare -n validator01
```

## What it does

Generates the DePool's seed phrase and key pair, derives its address, prints the phrase once and waits for `SAVED`.

## Flags

| Flag           | Value | Default  | Meaning                       |
| -------------- | ----- | -------- | ----------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.             |
| `--yes`        |       | ask      | Skip the confirmation prompt. |

## Behaviour

- DePool validators only; needs a terminal and stage `wallet-deployed`.
- Needs the vote engine that `host setup` installs: it derives the keys from each phrase, so no phrase is ever a process argument.
- Next: fund the printed address, then `depool deploy`.

## See also

- [depool deploy](deploy.md)
