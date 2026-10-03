import subprocess
import sys

from funlog import log_calls
from rich import get_console, reconfigure
from rich import print as rprint

# Update as needed.
SRC_PATHS = ["src", "tests", "devtools"]
DOC_PATHS = ["README.md"]


reconfigure(
    emoji=not get_console().options.legacy_windows
)  # No emojis on legacy windows.


def main() -> int:
    # Check mode reports what a fix would change instead of rewriting it, so a
    # CI run fails on drift rather than silently repairing the checkout.
    check = "--check" in sys.argv[1:]
    rprint()
    rprint(f"[bold cyan]Mode: {'check' if check else 'fix'}[/bold cyan]")

    errcount = 0
    errcount += run(
        ["codespell", *([] if check else ["--write-changes"]), *SRC_PATHS, *DOC_PATHS]
    )
    errcount += run(["ruff", "check", *([] if check else ["--fix"]), *SRC_PATHS])
    errcount += run(["ruff", "format", *(["--check"] if check else []), *SRC_PATHS])
    errcount += run(["basedpyright", "--stats", *SRC_PATHS])

    rprint()

    if errcount != 0:
        rprint(f"[bold red]:x: Lint failed with {errcount} errors.[/bold red]")
        if not check:
            rprint(
                "[bold yellow]Run `just lint`, then commit the result.[/bold yellow]"
            )
    else:
        rprint("[bold green]:white_check_mark: Lint passed![/bold green]")
    rprint()

    return errcount


@log_calls(level="warning", show_timing_only=True)
def run(cmd: list[str]) -> int:
    rprint()
    rprint(f"[bold green]>> {' '.join(cmd)}[/bold green]")
    errcount = 0
    try:
        subprocess.run(cmd, text=True, check=True)
    except KeyboardInterrupt:
        rprint("[yellow]Keyboard interrupt - Cancelled[/yellow]")
        errcount = 1
    except subprocess.CalledProcessError as e:
        rprint(f"[bold red]Error: {e}[/bold red]")
        errcount = 1

    return errcount


if __name__ == "__main__":
    exit(main())
