# evernode node sync-scripts

Refresh a node's saved scripts, configs and contracts from its source.

## Example

```bash
sudo evernode node sync-scripts --all --dry-run
sudo evernode node sync-scripts --all --yes
```

## What it does

Renders the node's recorded kit source again with the node's own settings and installs the files that differ.

## Flags

| Flag           | Value | Default       | Meaning                                           |
| -------------- | ----- | ------------- | ------------------------------------------------- |
| `-n`, `--name` | NAME  | the only node | One node.                                         |
| `--all`        |       | off           | Every managed node.                               |
| `--bundled`    |       | off           | Move the node to the kit installed with evernode. |
| `--yes`        |       | ask           | Skip the confirmation prompt.                     |
| `--dry-run`    |       | off           | Print the plan and change nothing.                |

## Behaviour

- Installing a new evernode package does not touch a node's scripts; this command does.
- The source is the one recorded at `node create`: the kit installed with evernode, a `--project-source` directory, or a fresh clone.
- A node created before evernode shipped its kit needs `--bundled` once; the plan shows the move first.
- When a recorded local kit no longer exists, the node is refused: move it with `--bundled`, or put the kit back.
- Replaced and removed files are copied to `.script-backups/TIMESTAMP/` in the node's workspace first.
- A node with an installed election schedule also gets this evernode version's election runner and sequence, which come with evernode, not with the kit.
- Keys, database, election state and logs are not touched, and the node keeps running.
- Refused for a node while a scheduled election run holds its lock; run it again a minute later.
- Exit code `1` if any node failed; the others are still done.
