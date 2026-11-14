# evernode nominator info

Show where a nominator validator's money is.

## Example

```bash
sudo evernode nominator info -n validator01
```

## What it does

Reads the contract, the Safe and the elector.

## Flags

| Flag           | Value | Default  | Meaning           |
| -------------- | ----- | -------- | ----------------- |
| `-n`, `--name` | NAME  | required | The managed node. |

## Behaviour

- `liquid_nanotokens`: the contract's balance. `withdrawable_nanotokens`: that minus the 1-token reserve.
- `recoverable_nanotokens`: what the elector owes the contract. Stake frozen at the elector is not shown.
- `wallet_gas_nanotokens`: the Safe's balance. Each bid and each recovery costs it about 2 tokens: keep it funded.
- `pending_wallet_transactions`: every Safe transaction short of signatures; `stake confirm` finishes one.
- Before setup is finished, `stake confirm` refuses: run `nominator deploy` again to finish its funding payment.

## See also

- [stake confirm](../stake/confirm.md)
- [nominator deploy](deploy.md)
