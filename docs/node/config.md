# evernode node config

Print a node's saved configuration.

## Example

```bash
sudo evernode node config -n validator01
```

## What it does

Prints `node.json`: ports, image, validator type, settings, wallet profile and setup stage.

## Flags

| Flag           | Value | Default       | Meaning                                             |
| -------------- | ----- | ------------- | --------------------------------------------------- |
| `-n`, `--name` | NAME  | the only node | The managed node; needed when the host has several. |

## Behaviour

- Holds addresses and settings, never keys or seed phrases.
- Read-only.
