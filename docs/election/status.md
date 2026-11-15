# evernode election status

Show a node's schedule and what the chain says about it.

## Example

```bash
sudo evernode election status -n validator01
```

## What it does

Reports whether the cron file exists, whether a cron daemon runs, and the node's own view of the validator sets.

## Flags

| Flag           | Value | Default  | Meaning           |
| -------------- | ----- | -------- | ----------------- |
| `-n`, `--name` | NAME  | required | The managed node. |

## Behaviour

- `enabled`: the schedule is installed. `cron_daemon`: `running`, `not running` or `unknown`.
- `chain`: `in_current_vset_p34`, `in_next_vset_p36`, `validation_status`, `validating`.
- `last_run`: when the latest scheduled run started and ended, its exit status and its last error lines. A proxy it found frozen, or without code (uninit or gone), is shown again as a red alarm.
- An installed schedule with no cron daemon never runs; the report warns.
- `in_next_vset_p36: false` during an election does not mean the bid failed: the next set is not chosen yet.

## See also

- [election list](list.md)
