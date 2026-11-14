# evernode wallet verify

Check an imported Safe's custodians and threshold again.

## Example

```bash
sudo evernode wallet verify -n validator01
```

## What it does

Reads the Safe on chain and compares it with the installed key files and the node's wallet profile.

## Flags

| Flag           | Value | Default  | Meaning           |
| -------------- | ----- | -------- | ----------------- |
| `-n`, `--name` | NAME  | required | The managed node. |

## Behaviour

- Needs stage `wallet-recovered`.
- DePool validator: stage becomes `wallet-verified`; next is `depool verify`.
- Solo and nominator: the import ends here with stage `imported`; next is `stake set` or `nominator prepare`.

## See also

- [wallet recover](recover.md)
- [depool verify](../depool/verify.md)
