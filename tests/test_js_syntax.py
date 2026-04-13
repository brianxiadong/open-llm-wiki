"""JS syntax validation gate.

Uses `node --check` to catch syntax errors in all static JS files before
they can reach production. This would have caught the missing `function
deleteSession(key) {` declaration that broke the entire chat interface.
"""

from __future__ import annotations

import glob
import os
import subprocess


def _js_files() -> list[str]:
    root = os.path.join(os.path.dirname(__file__), "..", "static", "js")
    return sorted(glob.glob(os.path.join(root, "**", "*.js"), recursive=True))


def test_js_files_found():
    """Ensure the test actually finds JS files to check."""
    assert len(_js_files()) > 0, "No JS files found under static/js/"


def test_js_syntax_valid():
    """Every JS file must pass `node --check` (syntax-only parse)."""
    errors: list[str] = []
    for path in _js_files():
        result = subprocess.run(
            ["node", "--check", path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            rel = os.path.relpath(path)
            errors.append(f"{rel}:\n{result.stderr.strip()}")

    assert not errors, "JS syntax errors found:\n\n" + "\n\n".join(errors)
