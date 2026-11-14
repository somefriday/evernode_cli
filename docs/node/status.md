# evernode node status

Show one node's containers, sync state and next setup step.

## Example

```bash
sudo evernode node status -n validator01
```

## What it does

Reads the saved configuration, the two containers and the node's own console statistics.

## Flags

| Flag           | Value | Default       | Meaning                                             |
| -------------- | ----- | ------------- | --------------------------------------------------- |
| `-n`, `--name` | NAME  | the only node | The managed node; needed when the host has several. |

## Behaviour

- `next_setup_command` is the command that continues setup; it is `null` once the election schedule is installed.
- `synchronized` is true when the node reports `synchronization_by_blocks` or `synchronization_finished` and both time differences are within 10 s either way.
- A negative time difference means this server's clock is behind the network's newest block; `clock` then says by how much.
- `stats` carries `in_current_vset_p34`, `in_next_vset_p36` and `validation_status`.
- A running container with a console that does not answer shows `console_error` instead of `stats`.

## See also

- [election status](../election/status.md)
- [doctor](../doctor/doctor.md)
