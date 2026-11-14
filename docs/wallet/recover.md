# evernode wallet recover

Rebuild an imported Safe's keys from its seed phrases and check them against the chain.

## Example

```bash
sudo evernode wallet recover -n validator01
```

## What it does

Derives a key pair from each stored phrase and compares the public keys and the signature threshold with the Safe on chain. Only then are the key files installed for the election scripts.

## Flags

| Flag              | Value | Default  | Meaning                                                 |
| ----------------- | ----- | -------- | ------------------------------------------------------- |
| `-n`, `--name`    | NAME  | required | The managed node.                                       |
| `--yes`           |       | ask      | Skip the confirmation prompt.                           |
| `--replace-seeds` |       | off      | Type the seed phrases again, replacing the stored ones. |

## Behaviour

- For nodes created with `--import-wallet`, at stage `node-started`.
- If no phrases are stored (a resumed create), they are asked for here, at a hidden prompt.
- A mistyped phrase fails this check; run it again with `--replace-seeds`.
- Never prints phrases, deploys anything or moves funds.

## See also

- [wallet verify](verify.md)
