"""Subprocess boundary: argument lists, explicit context and bounded cleanup."""

import os
import signal
import subprocess


class OperationError(Exception):
    pass


def execute_command(
    args, *, timeout=60, check=True, cwd=None, env=None, sensitive=False, stream=False
):
    argv = [str(a) for a in args]
    if not argv or any("\0" in a for a in argv):
        raise OperationError("Invalid command arguments")
    # Callers supply only non-secret args/env. Sensitive output is never included
    # in an exception (including TimeoutExpired, which otherwise prints argv).
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=None if stream else subprocess.PIPE,
            stderr=None if stream else subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise OperationError(
            f"Cannot start {os.path.basename(argv[0])}: {exc.strerror}"
        ) from None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        # Kill the process group, including shell children, before releasing locks.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise OperationError(
            f"{os.path.basename(argv[0])} timed out after {timeout}s"
        ) from None
    # Commands with stream=True inherit the operator's terminal. They cannot
    # provide captured diagnostics, but their output has already been shown.
    stdout = stdout or ""
    stderr = stderr or ""
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    if check and result.returncode:
        detail = "" if sensitive else (stderr or stdout).strip()
        raise OperationError(
            detail or f"{os.path.basename(argv[0])} failed (exit {result.returncode})"
        )
    return result
