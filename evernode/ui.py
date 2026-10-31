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


def select_choice(value, label, choices, *, flag, default=None):
    """Choose one of a fixed set: a flag if given, otherwise a terminal menu.

    Every fixed choice is presented the same way, so an operator never has to
    recall the exact spelling of a value and a typo cannot reach the node
    configuration. Noninteractive callers name the flag they are missing.
    """
    if value is not None:
        if value not in [item for item, _ in choices]:
            raise process.OperationError(
                f"Invalid {label}; choose one of: "
                + ", ".join(item for item, _ in choices)
            )
        return value
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        if default is not None:
            return default
        raise process.OperationError(
            f"Choose a {label} with {flag} in noninteractive mode"
        )
    _, index = select_option(
        f"Select {label}:",
        [description for _, description in choices],
        subject=label.capitalize(),
    )
    return choices[index][0]


def select_option(title, options, *, alternate=None, subject="Image"):
    """Return (page, index); Tab switches pages when an alternate is supplied."""
    if not options or not sys.stdin.isatty() or not sys.stdout.isatty():
        raise process.OperationError(f"{subject} selection requires a terminal")
    pages = [(title, options)] + ([alternate] if alternate else [])

    def numbered_choice():
        page = 0
        while True:
            heading, choices = pages[page]
            print(heading)
            for index, option in enumerate(choices, 1):
                print(f"  {index}. {option}")
            hint = (
                "number, Tab to switch, or blank to cancel"
                if alternate
                else "number or blank to cancel"
            )
            answer = input(f"Choose a {hint}: ").strip()
            if not answer:
                raise process.OperationError("Cancelled; no action taken")
            if alternate and answer.lower() in ("tab", "b"):
                page = 1 - page
            elif answer.isdigit() and 1 <= int(answer) <= len(choices):
                return page, int(answer) - 1

    try:
        import curses
    except ImportError:
        return numbered_choice()

    try:

        def choose(screen):
            curses.curs_set(0)
            screen.keypad(True)
            page = 0
            selected = [0] * len(pages)
            while True:
                screen.erase()
                height, width = screen.getmaxyx()
                if height < 4 or width < 20:
                    raise curses.error("Terminal is too small for the menu")
                heading, choices = pages[page]
                screen.addnstr(0, 0, heading, width - 1)
                first = max(
                    0,
                    min(
                        selected[page] - (height - 3) // 2, len(choices) - (height - 2)
                    ),
                )
                for row, index in enumerate(
                    range(first, min(len(choices), first + height - 2)), 1
                ):
                    label = ("▸ " if index == selected[page] else "  ") + choices[index]
                    screen.addnstr(row, 0, label, width - 1)
                footer = (
                    "↑/↓ select  Tab switch  Enter confirm  Esc cancel"
                    if alternate
                    else "↑/↓ select  Enter confirm  Esc cancel"
                )
                screen.addnstr(height - 1, 0, footer, width - 1)
                screen.refresh()
                key = screen.getch()
                if key == curses.KEY_UP:
                    selected[page] = (selected[page] - 1) % len(choices)
                elif key == curses.KEY_DOWN:
                    selected[page] = (selected[page] + 1) % len(choices)
                elif key == 9 and alternate:
                    page = 1 - page
                elif key in (curses.KEY_ENTER, 10, 13):
                    return page, selected[page]
                elif key == 27:
                    raise process.OperationError("Cancelled; no action taken")

        return curses.wrapper(choose)
    except (OSError, curses.error):
        return numbered_choice()
