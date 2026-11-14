# evernode image rebuild

Build a new image from the commits of an existing one.

## Example

```bash
sudo evernode image rebuild local/ever-node:05d556a38c1f2b1c --yes
```

## What it does

Builds again from the same recorded commits and records the result as a new image.

## Flags

| Flag        | Value                 | Default  | Meaning                            |
| ----------- | --------------------- | -------- | ---------------------------------- |
| IMAGE       | tag, id or key prefix | required | A managed image from `image list`. |
| `--yes`     |                       | ask      | Skip the confirmation prompt.      |
| `--dry-run` |                       | off      | Print the plan and change nothing. |

## Behaviour

- Always a new tag and record, even though the sources are the same.
- Attach it to a node with `node update`.

## See also

- [node update](../node/update.md)
