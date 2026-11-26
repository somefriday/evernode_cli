# evernode node update

Switch a node to another managed image, or back.

## Example

```bash
sudo evernode election stop -n validator01
sudo evernode node update -n validator01 --image local/ever-node:NEWTAG --yes
```

## What it does

Stops the node, rewrites its Compose definition for the new image, starts it and waits for it to synchronize.

## Flags

| Flag           | Value   | Default  | Meaning                                       |
| -------------- | ------- | -------- | --------------------------------------------- |
| `-n`, `--name` | NAME    | required | The managed node.                             |
| `--image`      | IMAGE   |          | Managed image to switch to.                   |
| `--rollback`   |         |          | Return to the image the last update replaced. |
| `--timeout`    | SECONDS | `30`     | Grace period for the stop.                    |
| `--yes`        |         | ask      | Skip the confirmation prompt.                 |
| `--dry-run`    |         | off      | Print the plan and change nothing.            |

## Behaviour

- One of `--image` and `--rollback` is required.
- Refuses while the election schedule can bid or the node is in a validator set; `--dry-run` lists these blockers and exits `1`. A denominator validator's pools' cycle, which `election stop` leaves running, waits for the update and runs again after it.
- Refuses while an election run that began before `election stop` is still using the node; retry when it finishes.
- Database, keys, wallet and configuration are kept.
- Success needs two synchronized readings within about a minute of the start. A node still catching up after that is reported as not synchronized: check `node sync --wait` before rolling back.
- The replaced image is remembered for `--rollback`, and `image remove` refuses it meanwhile.

## See also

- [image list](../image/list.md)
- [image rebuild](../image/rebuild.md)
