# evernode vote mint-wallet

Make a new wallet file to pay for proposals.

## Example

```bash
(umask 077; evernode vote mint-wallet --name payer > payer.json)
jq -r .address payer.json
```

## What it does

Generates a key, derives the masterchain address of its EverWallet and prints the wallet file as JSON.

## Flags

| Flag        | Value  | Default  | Meaning                                             |
| ----------- | ------ | -------- | --------------------------------------------------- |
| `--name`    | NAME   | required | A label for the wallet.                             |
| `--balance` | TOKENS | `0`      | Test networks only: balance of a zerostate account. |

## Behaviour

- Offline. The file holds the secret key: create it under `umask 077` and never commit it.
- Send the address the price plus 3 tokens; the wallet's first payment deploys it.
- `--secret` is refused: secrets are never taken as arguments.

## See also

- [vote propose](propose.md)
