# evernode node start

Start a node's containers.

## Example

```bash
sudo evernode node start -n validator01
```

## What it does

Starts the StatsD exporter, checks the node can reach it, initializes the node's identity on first start, starts the node and waits for its console.

## Flags

| Flag           | Value | Default       | Meaning                                             |
| -------------- | ----- | ------------- | --------------------------------------------------- |
| `-n`, `--name` | NAME  | the only node | The managed node; needed when the host has several. |
| `--dry-run`    |       | off           | Print the plan and change nothing.                  |

## Behaviour

- Restores a removed node: containers and network are made again from the saved configuration.
- A stopped node starts with this evernode version's container settings, such as the open-file limit, even if an older version created it. A running node keeps its settings until it is stopped.
- Refuses a node whose preparation did not finish; run `node create --resume -n NAME` first.
- Never regenerates keys: a half-written `node_cfg/` stops the start.
- Does not reinstall the election schedule that `node stop` or `node remove` removed: run `election start`.

## See also

- [node stop](stop.md)
- [election start](../election/start.md)
