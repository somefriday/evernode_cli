# evernode image list

Show managed images and which node uses which.

## Example

```bash
sudo evernode image list
```

## What it does

Prints every image record with its inputs, and each node's image.

## Flags

None.

## Behaviour

- Needs root.
- The `image` value is what `node create --image`, `node update --image` and `image remove` take.
