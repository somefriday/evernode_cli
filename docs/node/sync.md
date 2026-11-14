# evernode node sync

Report whether a node is synchronized, or wait until it is.

## Example

```bash
sudo evernode node sync -n validator01 --wait
```

## What it does

Prints the node's console statistics. With `--wait`, repeats until two readings in a row are synchronized.

## Flags

| Flag           | Value   | Default       | Meaning                                             |
| -------------- | ------- | ------------- | --------------------------------------------------- |
| `-n`, `--name` | NAME    | the only node | The managed node; needed when the host has several. |
| `--wait`       |         | off           | Poll until synchronized.                            |
| `--interval`   | SECONDS | `10`          | Time between polls.                                 |

## Behaviour

- Synchronized means the node reports `synchronization_by_blocks` or `synchronization_finished`, with masterchain and shard time differences within 10 s either way.
- A negative time difference means this server's clock is behind the network's newest block, not that the node is. Up to 10 s, the node counts as synchronized, with a warning to check the server's time sync (NTP). Beyond 10 s, the command reports the clock and exits `2` without waiting, even with `--wait`.
- Without `--wait`: exit code `0` when synchronized, `2` when not.
- With `--wait`, a console that refuses connections is waited for; any other error ends the command.
