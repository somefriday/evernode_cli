# evernode host setup

Install Docker, the tools the election scripts use and the vote engine, on Ubuntu or Debian.

## Example

```bash
sudo evernode host setup --yes
evernode host check
```

## What it does

Installs `ca-certificates`, `curl`, `git`, `jq`, `bc`, `gawk`, `rsync`, `util-linux`, `procps`, `cron` and `chrony` with apt, Docker Engine with Buildx and Compose from Docker's own repository when they are missing, and `yq` v4 to `/usr/local/bin/yq`. Then it enables `docker`, `cron` and `chrony`, and builds the vote engine `evernode-vote` into `/usr/local/bin` with Docker, from the sources installed with evernode.

## Flags

| Flag        | Value | Default | Meaning                            |
| ----------- | ----- | ------- | ---------------------------------- |
| `--yes`     |       | ask     | Skip the confirmation prompt.      |
| `--dry-run` |       | off     | Print the plan and change nothing. |

## Behaviour

- Needs root, Ubuntu or Debian with a known release codename, and network access.
- `/usr/local/bin/yq` is replaced on every run with the build for this machine's architecture.
- The vote engine is built only when it is missing or not the version this evernode needs, so run `host setup` again after each upgrade. A first build took about 9 minutes and 1 GB of memory on 2 CPUs; more CPUs build faster and use more memory, up to 3 GB.
- The firewall is not touched: open each node's ADNL UDP port yourself.
- It ends with the same check as `host check` and fails if anything is still missing.
- Safe to run again: an installed Docker is left as it is, and the other packages are upgraded when apt has a newer version.

## See also

- [host check](check.md)
