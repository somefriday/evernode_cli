# evernode nominator rotate

Prepare the owner's replacement of the hot wallet.

## Example

```bash
sudo evernode nominator rotate -n validator01 --validator-address -1:NEW_HOT_WALLET
```

## What it does

Builds the message body that makes another masterchain wallet the one allowed to bid and recover. Nothing is signed or sent.

## Flags

| Flag                  | Value  | Default  | Meaning                                         |
| --------------------- | ------ | -------- | ----------------------------------------------- |
| `-n`, `--name`        | NAME   | required | The managed node.                               |
| `--validator-address` | `-1:…` | required | The new hot wallet: masterchain, 64 hex digits. |

## Behaviour

- Once the owner's message executes, the contract ignores this node's Safe: stop this node's elections first.
- Stake at the elector and the node's validator keys are not moved.
- The contract's address does not change, so a node with another Safe cannot derive it with `nominator prepare`.

## See also

- [nominator submit](submit.md)
