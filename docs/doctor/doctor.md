# evernode doctor

Print one node's status with a few host facts.

## Example

```bash
sudo evernode doctor -n validator01
```

## What it does

Prints the `node status` report, then the free disk space of the node's workspace and whether its election cron file exists.

## Flags

| Flag           | Value | Default       | Meaning                                             |
| -------------- | ----- | ------------- | --------------------------------------------------- |
| `-n`, `--name` | NAME  | the only node | The managed node; needed when the host has several. |

## Behaviour

- It reports; it does not judge. For the schedule and the validator sets read `election status`.
- Failures of the node itself are in `node logs --component stderr`; of the exporter, in `--component statsd`.

## See also

- [node status](../node/status.md)
- [election status](../election/status.md)
