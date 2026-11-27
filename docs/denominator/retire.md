# evernode denominator retire

Remove a denominator validator's pool cycle once no stake is out.

## Example

```bash
sudo evernode denominator retire -n validator01
```

## What it does

Deletes `/etc/cron.d/evernode-NAME`, the schedule that keeps running after `election stop` to bring every stake back.

## Flags

| Flag           | Value | Default  | Meaning                       |
| -------------- | ----- | -------- | ----------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.             |
| `--yes`        |       | ask      | Skip the confirmation prompt. |

## Behaviour

- After `election stop`, on a synchronized node.
- Refuses while a pool's stake is out, the elector still owes a pool, or a pool has queued withdrawals: leave the cycle running and retry later.
- Refuses while the Safe holds an unsigned transaction to a pool, such as a half-signed bid. Signed after the cycle is gone, a bid would send a stake out that nothing brings back. Sign it with `stake confirm`, or let it expire, then retry.
- Stakers can still withdraw from an idle pool at any time with the `withdraw` link.
- The pools stay on chain, each with its 10-token reserve.
- `election start` installs the cycle again, with bidding.
- `node remove` refuses the node until this has run, unless `--force`.

## See also

- [election stop](../election/stop.md)
- [denominator info](info.md)
