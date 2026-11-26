# evernode node remove

Remove a node's containers, keeping its keys and configuration.

## Example

```bash
sudo evernode node remove -n validator01
```

## What it does

Removes the node's election cron file, stops the node, removes its two containers and its Docker network, and marks it `removed`.

## Flags

| Flag           | Value   | Default       | Meaning                                                                |
| -------------- | ------- | ------------- | ---------------------------------------------------------------------- |
| `-n`, `--name` | NAME    | the only node | The managed node; needed when the host has several.                    |
| `--all`        |         | off           | Every managed node.                                                    |
| `--purge-data` |         | off           | Also delete the database and logs.                                     |
| `--timeout`    | SECONDS | `30`          | Grace period for the stop.                                             |
| `--force`      |         | off           | Remove even while validating, with a bid pending, or running pools.    |
| `--yes`        |         | ask           | Skip the confirmation prompt.                                          |
| `--dry-run`    |         | off           | Print the plan and change nothing.                                     |

## Behaviour

- Keys, seed phrases, wallet files and `node.json` always stay.
- The name stays taken. `node start` brings the node back; nothing deletes a node entirely.
- Refuses while the node is in a validator set, unless `--force`.
- Refuses while the node has bid in an election whose result is not out yet, unless `--force`.
- Denominator: refuses while the pools' cycle is installed, unless `--force`: it brings their stakes back. Remove the node after `denominator retire`.

## See also

- [node start](start.md)
