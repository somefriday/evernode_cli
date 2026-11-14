"""Show and switch how much a node logs.

The node reads its log settings from node_cfg/log_cfg.yml. When the file sets
refresh_rate, the running node rereads it at that interval, so a switch needs
no restart.
"""

import re

from . import process, storage

LEVELS = ("info", "debug")
# The components the election kit makes verbose. A switch sets only these;
# every other component keeps the level the kit chose for it.
COMPONENTS = ("ever_node", "validator", "validator_session", "telemetry", "validate_query")
# A component's entry in the kit's layout: "  name:", then "    level: value".
ENTRY = re.compile(
    r"^(  (%s):\n    level: )([a-z]+)[ \t]*$" % "|".join(COMPONENTS), re.MULTILINE
)
REFRESH = re.compile(r"^refresh_rate:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _settings(store, name):
    folder = store.node_directory(name) / "node_cfg"
    path = folder / "log_cfg.yml"
    if not (path.exists() or path.is_symlink()):
        raise process.OperationError(
            "The node has no log settings yet; its first start makes them"
        )
    storage.validate_private_path(folder)
    storage.validate_private_path(path)
    return path, path.read_text()


def _levels(path, text):
    levels = {match.group(2): match.group(3) for match in ENTRY.finditer(text)}
    if "ever_node" not in levels:
        raise process.OperationError(
            f"{path} does not have the election kit's layout; change its levels by hand"
        )
    return levels


def read_level(store, name):
    """The level of the verbose components, or "custom" when they differ."""
    levels = set(_levels(*_settings(store, name)).values())
    return levels.pop() if len(levels) == 1 else "custom"


def set_level(store, name, level):
    """Switch the verbose components to LEVEL; returns when the node applies it."""
    if level not in LEVELS:
        raise process.OperationError("The log level must be info or debug")
    path, text = _settings(store, name)
    _levels(path, text)
    updated = ENTRY.sub(lambda match: match.group(1) + level, text)
    if updated != text:
        storage.write_private_text_atomically(path, updated)
    refresh = REFRESH.search(text)
    return f"within {refresh.group(1)}" if refresh else "at the next node start"
