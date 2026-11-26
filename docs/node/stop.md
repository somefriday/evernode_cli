# evernode node stop

Stop a node's containers and its election schedule, now or at a masterchain block.

## Example

```bash
evernode node stop -n validator01
evernode node stop -n validator01 --after-block 63500000
```

## What it does

Removes the node's election cron file, then stops the node and its exporter with a grace period. With `--after-block` it installs a cron file of its own instead, and cron stops the node once the node has applied that block.

## Flags

| Flag            | Value   | Default       | Meaning                                                              |
| --------------- | ------- | ------------- | -------------------------------------------------------------------- |
| `-n`, `--name`  | NAME    | the only node | The managed node; needed when the host has several.                  |
| `--all`         |         | off           | Every managed node.                                                  |
| `--after-block` | N       |               | Schedule the stop for masterchain block N, then show its progress.   |
| `--watch`       |         | off           | Show the scheduled stop's progress until it ends.                    |
| `--cancel`      |         | off           | Remove the scheduled stop; the node keeps running.                   |
| `--timeout`     | SECONDS | `30`          | Grace period before Docker kills the container.                      |
| `--force`       |         | off           | Stop even while the node is in a validator set or has a bid pending. |
| `--dry-run`     |         | off           | Print the plan and change nothing.                                   |

## Behaviour

- Refuses while the node reports itself in the current or next validator set.
- Refuses while the node has bid in an election whose result is not out yet: elected while stopped, it would miss that round.
- A console that does not answer cannot prove membership, so the stop goes ahead.
- Stake at the elector is not withdrawn; duties already accepted remain.
- Denominator: keeps the pools' cycle and turns its bids off, as `election stop` does. The cycle runs again once the node starts; `election start` turns the bids back on.
- `--after-block`, `--watch` and `--cancel` take one node, not `--all`, and only one of them at a time.
- `--after-block` makes both refusals at once, and keeps `--force` and `--timeout` for the stop. It needs a console that answers, a cron daemon, and a node still below block N: a node at or past N is refused.
- Cron stops the node 30 s after its console reports block N and every shard block N lists as applied. It tries once; a stop that fails is in the log and nothing stays scheduled.
- A node is never stopped late. One found at or past block N that was last seen below it more than 10 minutes ago is left running; the log says so and nothing stays scheduled.
- The progress is one line a minute. Ctrl+C ends the display with exit code 130; the stop stays scheduled.
- `--after-block` and `--watch` end with exit code 0 when cron stopped the node, 1 when the scheduled stop ended any other way.
- Every other stop of the node removes its scheduled stop: `node stop`, `node restart`, `node remove`, `node update`, `node lsync`.
- The log is `logs/validator/stop-at-block.log` in the node's directory.

## See also

- [node start](start.md)
- [election status](../election/status.md)
- [Export the state of a masterchain block](../README.md#maintenance)
