"""Call the Obsidian CLI and treat its failures as failures.

The CLI takes ``key=value`` arguments and exits 0 even when a command fails;
it reports the failure as a stdout line that starts with ``Error:``. The
binary is ``MIMIR_OBSIDIAN`` when that is set, else ``obsidian``.
"""

import os
import subprocess
import time
from pathlib import Path


# Seconds to wait before a new note is read back; tests set it to 0.
REREAD_DELAY = 1.0

# Right after a move into a new folder the app may not know the folder yet,
# and ``create`` then fails with this line. It is retried CREATE_RETRIES
# times, RETRY_DELAY seconds apart; tests set the delay to 0.
FOLDER_RACE = "Folder already exists"
CREATE_RETRIES = 3
RETRY_DELAY = 1.0


class CliError(Exception):
    """The Obsidian CLI could not run, exited non-zero or printed Error:."""


def binary() -> str:
    return os.environ.get("MIMIR_OBSIDIAN") or "obsidian"


def run(args: list[str], cwd: Path | None = None) -> str:
    """Run the CLI with args and stdin closed; its stdout on success.

    cwd is the directory the CLI runs in; the app acts on the vault that
    holds it, so callers pass the vault.
    """
    command = [binary(), *args]
    try:
        done = subprocess.run(command, stdin=subprocess.DEVNULL, cwd=cwd,
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


def create_note(vault: Path, rel: str, text: str) -> Path:
    """Create the note rel in vault with exactly text, through the app.

    ``content=`` would turn every backslash-n in the text into a newline, so
    the app creates the file and the bytes are written to it directly. After
    REREAD_DELAY seconds the file is read back; when it differs, a folder
    template got there first and CliError is raised.
    """
    vault = Path(vault)
    path = vault / rel
    os.makedirs(path.parent, exist_ok=True)
    for attempt in range(CREATE_RETRIES + 1):
        try:
            run(["create", f"path={rel}"], cwd=vault)
            break
        except CliError as err:
            if FOLDER_RACE not in str(err) or attempt == CREATE_RETRIES:
                raise
            time.sleep(RETRY_DELAY)
    data = text.encode("utf-8")
    path.write_bytes(data)
    time.sleep(REREAD_DELAY)
    if path.read_bytes() != data:
        raise CliError(f"{rel}: the file changed after it was written "
                       "(a folder template filled it?)")
    return path
