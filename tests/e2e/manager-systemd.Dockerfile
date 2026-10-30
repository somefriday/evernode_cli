# The same server with an init system, so `evernode host setup` can enable the
# docker, cron and chrony services it requires and `host check` can see them.
# Run it as: docker run -d --privileged --cgroupns=host \
#   -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock <image>
ARG BASE=evernode-prod:ubuntu24
FROM ${BASE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y systemd systemd-sysv && rm -rf /var/lib/apt/lists/*
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
