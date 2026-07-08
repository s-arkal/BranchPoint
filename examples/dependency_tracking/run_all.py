"""Run all dependency-tracking examples."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


EXAMPLES = [
    "01_auto_decorator_provenance.py",
    "02_depends_on_prompt_string.py",
    "03_prompt_builder.py",
    "04_field_level_proxy.py",
    "05_manual_emit_and_refs.py",
    "06_reserved_kwargs.py",
]


def main() -> None:
    here = Path(__file__).resolve().parent
    for example in EXAMPLES:
        print(f"Running {example} ... ", end="", flush=True)
        result = subprocess.run(
            [sys.executable, str(here / example)],
            cwd=here.parents[1],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            print("FAILED")
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            raise SystemExit(result.returncode)
        print("OK")
    print("All dependency-tracking examples passed.")


if __name__ == "__main__":
    main()
