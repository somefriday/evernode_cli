# evernode election start

Install a node's election schedule.

## Example

```bash
sudo evernode election start -n validator01
```

## What it does

Writes `/etc/cron.d/evernode-NAME`, which runs the node's election sequence every interval under a lock.

## Flags

| Flag           | Value | Default  | Meaning                            |
| -------------- | ----- | -------- | ---------------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.                  |
| `--yes`        |       | ask      | Skip the confirmation prompt.      |
| `--dry-run`    |       | off      | Print the plan and change nothing. |

## Behaviour

- Needs a finished setup (stage `ready-for-elections` or `imported`), a synchronized node and, for solo and nominator, a recorded stake; for nominator also the tools image `nominator prepare` builds.
- DePool sequence: prepare (tick the DePool), wait 120 s, bid. A failed preparation stops the run.
- Preparation tops up the DePool and its two proxies from the Safe when they run low. A proxy gets 5 tokens plus the storage it owes once its balance less that storage falls under 1.92 tokens; one that owes more than 50 tokens gets nothing, and the log names what it needs. If a top-up fails while the proxy owes more than it holds, the run stops before the tick.
- On a DePool node the command tops up the proxies the same way before it installs the schedule; such a failed top-up stops it with nothing installed.
- While one of the DePool's proxies does not read as Active, the command is refused and each scheduled run stops before the tick; `election status` shows why. A frozen one, or one without code (uninit or gone), gets a red alarm. A frozen one needs the state it had when it froze, its code and data, sent with the tokens it owes; one without code cannot be rebuilt.
- Solo and nominator sequence: recover finished rounds, then bid; a failed recovery does not cancel the bid.
- A bid that does not register is sent again by the next scheduled run, in the same election: three runs at most. No retry when the DePool's round being elected holds no stake, which a retry cannot change. To allow more runs in that election, delete `elections/ID/ID_bid-stopped.txt` in the node's workspace.
- A run is skipped, with a `skipped` line in the log, while another run or an evernode command holds the node's election lock. Output goes to `node logs --component election-cron`.
- The interval is the one chosen at `node setup`. Refuses if a schedule is already installed.

## See also

- [election status](status.md)
- [node logs](../node/logs.md)
