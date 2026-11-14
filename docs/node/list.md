# evernode node list

Show every managed node and its state.

## Example

```bash
sudo evernode node list
```

## What it does

Prints the `node status` report of each node as one JSON array.

## Flags

None.

## Behaviour

- Needs root.
- Removed nodes stay in the list with `phase: removed`.
- A node whose console does not answer is listed with `console_error`.

## See also

- [node status](status.md)
