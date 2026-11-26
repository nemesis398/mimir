"""Call the Obsidian CLI and treat its failures as failures.

The CLI takes ``key=value`` arguments and exits 0 even when a command fails;
it reports the failure as a stdout line that starts with ``Error:``. The
binary is ``MIMIR_OBSIDIAN`` when that is set, else ``obsidian``.
"""

import os
import subprocess


class CliError(Exception):
    """The Obsidian CLI could not run, exited non-zero or printed Error:."""


def binary() -> str:
    return os.environ.get("MIMIR_OBSIDIAN") or "obsidian"


def run(args: list[str]) -> str:
    """Run the CLI with args and stdin closed; its stdout on success."""
    command = [binary(), *args]
    try:
        done = subprocess.run(command, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except OSError as err:
        raise CliError(f"cannot run {command[0]}: {err}") from None
    shown = " ".join(args)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()
        raise CliError(f"obsidian {shown}: exit {done.returncode}: {detail}")
    for line in done.stdout.splitlines():
        if line.lstrip().startswith("Error:"):
            raise CliError(f"obsidian {shown}: {line.strip()}")
    return done.stdout
