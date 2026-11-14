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
# Either line may end in a comment, which a switch keeps.
COMMENT = r"(?:[ \t]+#[^\n]*)?"
ENTRY = re.compile(
    r"^(  (%s):%s[ \t]*\n    level:[ \t]+)([A-Za-z]+)(%s[ \t]*)$"
    % ("|".join(COMPONENTS), COMMENT, COMMENT),
    re.MULTILINE,
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
    try:
        return path, path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise process.OperationError(
            f"{path} is not UTF-8 text; change its levels by hand"
        ) from None


def _levels(path, text):
    """Each component's level, read before anything changes: a component this
    cannot read once, in the kit's layout, refuses the whole file, so a switch
    never reports success with one of them left behind."""
    found = {}
    for match in ENTRY.finditer(text):
        found.setdefault(match.group(2), []).append(match.group(3).lower())
    unread = [component for component in COMPONENTS if len(found.get(component, ())) != 1]
    if unread:
        raise process.OperationError(
            f"{path}: the level of {', '.join(unread)} is not in the election kit's "
            "layout; change the levels by hand"
        )
    return {component: found[component][0] for component in COMPONENTS}


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
    updated = ENTRY.sub(lambda match: match.group(1) + level + match.group(4), text)
    if updated != text:
        storage.write_private_text_atomically(path, updated)
    refresh = REFRESH.search(text)
    return f"within {refresh.group(1)}" if refresh else "at the next node start"
