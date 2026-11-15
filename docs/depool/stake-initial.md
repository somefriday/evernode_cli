# evernode depool stake-initial

Stake the validator assurance from the Safe into the DePool.

## Example

```bash
sudo evernode depool stake-initial -n validator01
```

## What it does

Sends an ordinary stake of the configured validator assurance, signs it with the local custodian keys and waits until the DePool shows it.

## Flags

| Flag           | Value | Default  | Meaning                       |
| -------------- | ----- | -------- | ----------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.             |
| `--yes`        |       | ask      | Skip the confirmation prompt. |

## Behaviour

- Needs stage `depool-deployed`; ends at `ready-for-elections`.
- Refused, with a red alarm, while one of the DePool's proxies is frozen: send the proxy its original state with the tokens it owes, then run the command again.
- Safe to run again: a stake already in the DePool is not sent twice, and a half-signed one is finished.
- One stake funds every other round. To validate every round, add a second one with `depool stake` while the next round is collecting.

## See also

- [depool stake](stake.md)
- [election start](../election/start.md)
