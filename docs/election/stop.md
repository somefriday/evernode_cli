# evernode election stop

Remove a node's election schedule.

## Example

```bash
sudo evernode election stop -n validator01
```

## What it does

Deletes `/etc/cron.d/evernode-NAME`.

## Flags

| Flag           | Value | Default       | Meaning                                             |
| -------------- | ----- | ------------- | --------------------------------------------------- |
| `-n`, `--name` | NAME  | the only node | The managed node; needed when the host has several. |
| `--all`        |       | off           | Every managed node.                                 |
| `--yes`        |       |               | Accepted and ignored; stopping never asks.          |
| `--dry-run`    |       | off           | Print the plan and change nothing.                  |

## Behaviour

- No new bids are made. A run already in progress finishes, and may still place its bid.
- `node update`, `node restart` and `node lsync` refuse until that run has finished.
- Stake at the elector and sets the node is already in are not affected.
- The node is free to stop once `election status` shows it outside both validator sets.
- Denominator: stops the bids only. The pools' cycle keeps running, so every stake comes back and queued withdrawals are paid; `denominator retire` removes it.
- Denominator: `wallet send`, `node restart` and `node update` no longer wait once bidding is stopped; `node remove` waits for `denominator retire`.

## See also

- [election start](start.md)
- [node stop](../node/stop.md)
