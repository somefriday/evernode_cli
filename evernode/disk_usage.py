"""Allocated workspace sizes, measured without reading file contents."""

from pathlib import Path

from . import process

DISK_SCAN_TIMEOUT = 120


def measure_workspace(directory):
    """Use one GNU du scan, counting sparse allocation and hard links correctly.

    Return top-level directory sizes and the workspace total in bytes. Symlinks
    are not followed. A failed or interrupted scan never returns partial totals.
    """
    directory = Path(directory)
    result = process.execute_command(
        ["du", "--block-size=1", "--max-depth=1", "--null", "--", directory],
        timeout=DISK_SCAN_TIMEOUT,
    )
    sizes = {}
    try:
        for record in result.stdout.split("\0"):
            if not record:
                continue
            size, path = record.split("\t", 1)
            path = Path(path)
            size = int(size)
            if size < 0 or (path != directory and path.parent != directory):
                raise ValueError("Unexpected disk usage record")
            sizes[path] = size
        total = sizes[directory]
        if sum(size for path, size in sizes.items() if path != directory) > total:
            raise ValueError("Inconsistent disk usage totals")
    except (KeyError, ValueError) as exc:
        raise process.OperationError(
            "Could not read a complete disk usage measurement"
        ) from exc
    return sizes


def format_bytes(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024 or unit == "PiB":
            return f"{size:.1f} {unit}"
        size /= 1024
