# evernode nominator link

Make a wallet link that sends a body to the nominator contract, for the owner to confirm.

## Example

```bash
sudo evernode nominator link -n validator01
```

## What it does

Puts this node's contract address, the tokens to attach and the message body into an Oberton
wallet link and prints it. Without flags the link withdraws every liquid token above the
contract's reserve to the owner.

## Flags

| Flag           | Value  | Default                         | Meaning                                       |
| -------------- | ------ | ------------------------------- | --------------------------------------------- |
| `-n`, `--name` | NAME   | required                        | The managed node.                             |
| `--value`      | TOKENS | `2`                             | Whole tokens to attach.                       |
| `--body`       | BASE64 | the withdraw-everything comment | Message body, a base64 BOC.                   |

## Behaviour

- Nominator validators only, after `nominator prepare`: the contract's address comes from it.
- Reads nothing from the chain and needs no running node. Nothing is signed or sent, and the link
  holds no secret.
- Open the link, or a QR code of it, in the wallet app that holds the owner wallet, and confirm
  there. The contract ignores every other sender.
- The default body is the comment `w` as the contract reads it, the one `nominator withdraw --all`
  builds. A comment typed in a wallet app is written another way, which the contract refuses.
- Oberton sends the transfer without bounce: tokens sent with a body the contract refuses stay in
  the contract.
- A withdrawal returns what it does not use of the attached tokens. Stake still held by the
  elector is not withdrawn.

## See also

- [nominator withdraw](withdraw.md)
- [nominator info](info.md)
