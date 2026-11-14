# evernode image remove

Delete a managed image that nothing uses.

## Example

```bash
sudo evernode image remove local/ever-node:OLDTAG --yes
```

## What it does

Removes the Docker image and its record.

## Flags

| Flag        | Value                 | Default  | Meaning                            |
| ----------- | --------------------- | -------- | ---------------------------------- |
| IMAGE       | tag, id or key prefix | required | A managed image from `image list`. |
| `--yes`     |                       | ask      | Skip the confirmation prompt.      |
| `--dry-run` |                       | off      | Print the plan and change nothing. |

## Behaviour

- Refuses while a node uses the image or keeps it for `node update --rollback`, or while any container uses it.
- Removed nodes do not hold their image: `node start` on one needs the image to exist.

## See also

- [image list](list.md)
