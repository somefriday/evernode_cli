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


def select_option(title, options):
    """Select an option with arrow keys; use numbers on unsupported terminals."""
    if not options or not sys.stdin.isatty() or not sys.stdout.isatty():
        raise process.OperationError("Image selection requires a terminal")

    def numbered_choice():
        print(title)
        for index, option in enumerate(options, 1):
            print(f"  {index}. {option}")
        while True:
            answer = input("Choose a number (blank to cancel): ").strip()
            if not answer:
                raise process.OperationError("Cancelled; no action taken")
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer) - 1

    try:
        import curses
    except ImportError:
        return numbered_choice()

    try:

        def choose(screen):
            curses.curs_set(0)
            screen.keypad(True)
            selected = 0
            while True:
                screen.erase()
                height, width = screen.getmaxyx()
                if height < 4 or width < 20:
                    raise curses.error("Terminal is too small for the image menu")
                screen.addnstr(0, 0, title, width - 1)
                first = max(
                    0, min(selected - (height - 3) // 2, len(options) - (height - 2))
                )
                for row, index in enumerate(
                    range(first, min(len(options), first + height - 2)), 1
                ):
                    label = ("▸ " if index == selected else "  ") + options[index]
                    screen.addnstr(row, 0, label, width - 1)
                screen.addnstr(
                    height - 1, 0, "↑/↓ select  Enter confirm  Esc cancel", width - 1
                )
                screen.refresh()
                key = screen.getch()
                if key == curses.KEY_UP:
                    selected = (selected - 1) % len(options)
                elif key == curses.KEY_DOWN:
                    selected = (selected + 1) % len(options)
                elif key in (curses.KEY_ENTER, 10, 13):
                    return selected
                elif key == 27:
                    raise process.OperationError("Cancelled; no action taken")

        return curses.wrapper(choose)
    except (OSError, curses.error):
        return numbered_choice()
