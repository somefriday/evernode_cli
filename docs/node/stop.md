# evernode node stop

Stop a node's containers and its election schedule.

## Example

```bash
sudo evernode node stop -n validator01
```

## What it does

Removes the node's election cron file, then stops the node and its exporter with a grace period.

## Flags

| Flag           | Value   | Default       | Meaning                                                              |
| -------------- | ------- | ------------- | -------------------------------------------------------------------- |
| `-n`, `--name` | NAME    | the only node | The managed node; needed when the host has several.                  |
| `--all`        |         | off           | Every managed node.                                                  |
| `--timeout`    | SECONDS | `30`          | Grace period before Docker kills the container.                      |
| `--force`      |         | off           | Stop even while the node is in a validator set or has a bid pending. |
| `--dry-run`    |         | off           | Print the plan and change nothing.                                   |

## Behaviour

- Refuses while the node reports itself in the current or next validator set.
- Refuses while the node has bid in an election whose result is not out yet: elected while stopped, it would miss that round.
- A console that does not answer cannot prove membership, so the stop goes ahead.
- Stake at the elector is not withdrawn; duties already accepted remain.

## See also

- [node start](start.md)
- [election status](../election/status.md)
