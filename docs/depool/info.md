# evernode depool info

Show the DePool's rounds, this node's stake and pending Safe transactions.

## Example

```bash
sudo evernode depool info -n validator01
```

## What it does

Reads the DePool's getters and the Safe's pending transactions through the node's console.

## Flags

| Flag           | Value | Default  | Meaning           |
| -------------- | ----- | -------- | ----------------- |
| `-n`, `--name` | NAME  | required | The managed node. |

## Behaviour

- Available from stage `depool-deployed`.
- `rounds`: each round's `step`, `stake` and `supposedElectedAt`. `own_total_nanotokens`, `own_pending_withdrawal_nanotokens` and `own_reinvest` describe this node's stake.
- `self_balance_tokens` is the DePool's own operating balance; top it up with `depool replenish`.
- `pending_wallet_transactions` lists Safe transactions short of signatures, with the id `depool confirm` takes.

## See also

- [depool confirm](confirm.md)
