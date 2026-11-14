# evernode nominator submit

Broadcast a message the owner signed.

## Example

```bash
sudo evernode nominator submit -n validator01 --message /root/owner-signed.boc
```

## What it does

Copies the signed message into the node's workspace and sends it through the node's console.

## Flags

| Flag           | Value | Default  | Meaning                            |
| -------------- | ----- | -------- | ---------------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.                  |
| `--message`    | PATH  | required | The signed external message (BOC). |
| `--yes`        |       | ask      | Skip the confirmation prompt.      |

## Behaviour

- Submission is not execution: check `nominator info` for the effect.
- An external message expires about a minute after signing; sign right before submitting.
- An expired or unauthorized message is ignored by the chain.

## See also

- [nominator info](info.md)
