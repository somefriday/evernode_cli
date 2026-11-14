# evernode host check

Report whether this host has what evernode needs.

## Example

```bash
evernode host check
```

## What it does

Looks for the required commands, asks Docker and Compose for their versions, and asks systemd whether `docker`, `cron` and `chrony` are active. It installs and changes nothing.

## Flags

None.

## Behaviour

- Needs no root.
- Commands checked: `docker`, `git`, `tail`, `jq`, `bc`, `flock`, `cron`, `chronyc`, `gawk`, `rsync`, `yq`.
- Exit code `0` when everything is present and the three services are active, otherwise `1`.
- Services are read with `systemctl is-active`, so a host without systemd reports them as not active.

## See also

- [host setup](setup.md)
