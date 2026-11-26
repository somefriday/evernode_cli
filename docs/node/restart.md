# evernode node restart

Stop and start a node.

## Example

```bash
sudo evernode node restart -n validator01
```

## What it does

Runs `node stop` and `node start` under one lock.

## Flags

| Flag           | Value   | Default       | Meaning                                             |
| -------------- | ------- | ------------- | --------------------------------------------------- |
| `-n`, `--name` | NAME    | the only node | The managed node; needed when the host has several. |
| `--timeout`    | SECONDS | `30`          | Grace period for the stop.                          |
| `--force`      |         | off           | Restart even while the node is in a validator set.  |
| `--dry-run`    |         | off           | Print the plan and change nothing.                  |

## Behaviour

- Refuses while the election schedule can bid: run `election stop` first and `election start` afterwards. A denominator validator's pools' cycle, which `election stop` leaves running, waits for the restart and runs again after it.
- Refuses while an election run that began before `election stop` is still using the node; retry when it finishes.
- Refuses while the node is in the current or next validator set, unless `--force`.
- Refuses a node that was removed or never finished preparation.

## See also

- [election stop](../election/stop.md)
