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
- Refused while one of the DePool's proxies does not read as Active. A frozen one, or one without code (uninit or gone), gets a red alarm; a frozen one needs the state it had when it froze, its code and data, sent with the tokens it owes. Right after `depool deploy` a proxy may still be on its way: run the command again a minute later.
- Safe to run again: a stake already in the DePool is not sent twice, and a half-signed one is finished.
- One stake funds every other round. To validate every round, add a second one with `depool stake` while the next round is collecting.

## See also

- [depool stake](stake.md)
- [election start](../election/start.md)
