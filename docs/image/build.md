# evernode image build

Build a node image from source.

## Example

```bash
sudo evernode image build --node-ref master --yes
```

## What it does

Clones ever-node and ever-cli at the given refs, builds them in Docker with the pinned Rust toolchain and records the image with the commits it was built from.

## Flags

| Flag             | Value   | Default              | Meaning                                               |
| ---------------- | ------- | -------------------- | ----------------------------------------------------- |
| `--node-repo`    | URL     | everx-labs/ever-node | ever-node repository.                                 |
| `--node-ref`     | REF     | `master`             | Branch, tag or commit.                                |
| `--cli-repo`     | URL     | everx-labs/ever-cli  | ever-cli repository.                                  |
| `--cli-ref`      | REF     | `0.44.0`             | Branch, tag or commit.                                |
| `--image-repo`   | NAME    | `local/ever-node`    | Image repository; the tag is derived from the inputs. |
| `--rust-version` | VERSION | `1.90.0`             | Rust toolchain inside the builder.                    |
| `--yes`          |         | ask                  | Skip the confirmation prompt.                         |
| `--dry-run`      |         | off                  | Print the plan and change nothing.                    |

## Behaviour

- A build takes a long time (up to two hours is allowed); run it in `tmux`.
- The same inputs give the same record: an existing image is reused, not rebuilt.
- The image is checked for the binaries the node and scripts need before it is recorded.

## See also

- [image list](list.md)
- [image rebuild](rebuild.md)
