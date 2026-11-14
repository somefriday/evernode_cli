# evernode node log-level

Show or switch how much a node logs.

## Example

```bash
sudo evernode node log-level -n validator01 debug
sudo evernode node log-level --all info
```

## What it does

Sets the five components the election kit makes verbose (`ever_node`, `validator`, `validator_session`, `telemetry`, `validate_query`) to `info` or `debug` in the node's `node_cfg/log_cfg.yml`. Without a level, prints the current one. The running node rereads that file every 30 s, so the switch needs no restart.

## Flags

| Flag           | Value          | Default       | Meaning                                             |
| -------------- | -------------- | ------------- | --------------------------------------------------- |
| `-n`, `--name` | NAME           | the only node | The managed node; needed when the host has several. |
| `--all`        |                | off           | Every managed node.                                 |
| LEVEL          | `info` `debug` | shows it      | The level to switch to.                             |

## Behaviour

- Needs root.
- A node created from the kit installed with evernode starts at `info`. On a test network, `debug` wrote 1.8 GB in 12 hours.
- Every other component keeps the level the kit set, such as `error` for the network and `off` for the virtual machine.
- Prints `custom` when the five components were set to different levels by hand; a switch sets them all.
- A stopped node applies the switch when it starts. A file without `refresh_rate` applies it only at the next start; `applies` in the output says so.
- Refuses a node that has not started yet, and a file without the kit's layout: change that file by hand.
- With `--all`, a node that fails is reported and the others still switch; the exit code is then `1`.

## See also

- [node logs](logs.md)
