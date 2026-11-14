# evernode wallet create

Generate the node's Safe multisig wallet.

## Example

```bash
sudo evernode wallet create -n validator01
```

## What it does

Generates one seed phrase and key pair per custodian, derives the Safe's address, prints the phrases once and waits for you to type `SAVED`.

## Flags

| Flag           | Value | Default  | Meaning                       |
| -------------- | ----- | -------- | ----------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.             |
| `--yes`        |       | ask      | Skip the confirmation prompt. |

## Behaviour

- Needs an interactive terminal, a synchronized node and setup stage `node-started`.
- Needs the vote engine that `host setup` installs: it derives the keys from each phrase, so no phrase is ever a process argument.
- Phrases go to the terminal only, never to JSON output or logs. They are also kept root-only in the node's `keys/`.
- Without `SAVED` the stage does not advance; running the command again shows the same phrases.
- Next: fund the printed address, then `wallet deploy`.

## See also

- [wallet deploy](deploy.md)
