# evernode node logs

Read one of a node's logs.

## Example

```bash
sudo evernode node logs -n validator01 --component election-cron --tail 100 --follow
```

## What it does

Runs `tail` on the log file, or `docker logs` for the container components.

## Flags

| Flag           | Value                                                                   | Default       | Meaning                                             |
| -------------- | ----------------------------------------------------------------------- | ------------- | --------------------------------------------------- |
| `-n`, `--name` | NAME                                                                    | the only node | The managed node; needed when the host has several. |
| `--component`  | `node` `stderr` `stdout` `startup` `statsd` `elections` `election-cron` | `node`        | Which log.                                          |
| `--tail`       | LINES                                                                   | `100`         | Lines from the end.                                 |
| `--follow`     |                                                                         | off           | Keep printing new lines; Ctrl+C stops.              |

## Behaviour

- `node`: the node's own log. `stderr`: normally empty; a panic or start failure lands here.
- `election-cron`: every scheduled election run. `elections`: the scripts' record of pending Safe transactions.
- `startup` and `statsd` are the containers' Docker logs.
- Without `--follow`, a log that does not exist yet is an error; with it, `tail` waits for the file.
