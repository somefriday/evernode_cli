"""Terminal prompts and JSON output shared by CLI command handlers."""

import json
import sys

from . import process


def print_json_result(value):
    print(json.dumps(value, indent=2))


def prompt_for_value(value, label, default=None):
    if value is not None:
        return value
    if not sys.stdin.isatty():
        if default is not None:
            return default
        raise process.OperationError(
            f"Missing {label}; supply its command flag in noninteractive mode"
        )
    answer = input(label + (f" [{default}]" if default else "") + ": ").strip()
    return answer or default or prompt_for_value(None, label)


def confirm_operation(yes, text):
    if yes:
        return
    if not sys.stdin.isatty() or input(text + " [y/N]: ").strip().lower() not in (
        "y",
        "yes",
    ):
        raise process.OperationError(
            "Cancelled; no action taken (use --yes for explicit noninteractive approval)"
        )


def prompt_integer(value, label, default):
    raw = prompt_for_value(None if value is None else str(value), label, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise process.OperationError(f"{label} must be an integer") from exc
