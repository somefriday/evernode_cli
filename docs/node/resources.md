# evernode node resources

Show CPU, memory, I/O and disk use.

## Example

```bash
sudo evernode node resources -n validator01
```

## What it does

Prints one `docker stats` table for the node and its exporter, then the disk space of the node's workspace.

## Flags

| Flag           | Value | Default   | Meaning   |
| -------------- | ----- | --------- | --------- |
| `-n`, `--name` | NAME  | all nodes | One node. |

## Behaviour

- Disk columns: `DATABASE` (`node_db/`), `LOGS`, `OTHER`, `TOTAL`, then one summary of capacity and free space.
- Disk use is reported even when the containers are stopped.
- A container Docker reports as OOM-killed gets a warning line.
- Exit code `1` if any node could not be measured; the others are still shown.
