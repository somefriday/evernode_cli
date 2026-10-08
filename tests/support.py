"""Reusable, non-secret node metadata for isolated tests."""


def make_node_configuration(name="node-01", **changes):
    value = dict(
        schema_version=1,
        name=name,
        statsd="statsd-" + name,
        image="local/node:1",
        image_id="sha256:" + "a" * 64,
        network="main",
        ip="203.0.113.1",
        memory="40G",
        adnl_port=58888,
        metrics_port=9102,
        phase="prepared",
        initialized=False,
    )
    value.update(changes)
    return value
