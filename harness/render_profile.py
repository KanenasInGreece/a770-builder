#!/usr/bin/env python3
"""A Python renderer for the opencode profile, replacing the sed, with three new placeholders at their defaults."""

import argparse
import json
import os
import sys
from pathlib import Path


# The default prompt text (as specified in the brief)
DEFAULT_PROMPT = (
    "You are a coding agent working inside a git worktree of a Python project. Use the tools to read, search (grep/glob), "
    "edit and run commands. Read the brief you are pointed at, locate code with grep before reading whole files, "
    "make the change, run the exact test command given, and report the result. Be concise; do not narrate plans."
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the opencode profile template")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render", help="Render the profile from template")
    render_parser.add_argument("--template", "-T", required=True, help="Path to template file")
    render_parser.add_argument("--out", "-O", required=True, help="Output file path")
    render_parser.add_argument("--baseurl", "-u", required=True, help="Base URL")
    render_parser.add_argument("--apikey", "-k", required=True, help="API key")
    render_parser.add_argument("--ctx", "-c", required=True, help="Context window size")
    render_parser.add_argument("--output", "-m", required=True, help="Output window size")
    render_parser.add_argument("--name", "-n", required=True, help="Agent name")

    args = parser.parse_args()

    # Read the template file as UTF-8 text
    template_path = Path(args.template)
    text = template_path.read_text(encoding="utf-8")

    # Replace placeholders with given values
    text = text.replace("__BASEURL__", args.baseurl)
    text = text.replace("__APIKEY__", args.apikey)
    text = text.replace("__CTX__", args.ctx)
    text = text.replace("__OUTPUT__", args.output)
    text = text.replace("__NAME__", args.name)

    # Replace __PROMPT__ with DEFAULT_PROMPT, JSON-escaped and WITHOUT surrounding quotes
    json_escaped_prompt = json.dumps(DEFAULT_PROMPT)[1:-1]
    text = text.replace("__PROMPT__", json_escaped_prompt)

    # Replace __EDIT_RULES__ with exactly `"*": "allow"`
    text = text.replace("__EDIT_RULES__", '"*": "allow"')

    # Replace __BASH_ALLOW__ with the empty string
    text = text.replace("__BASH_ALLOW__", "")

    # Check for any remaining unreplaced placeholders
    import re
    pattern = r"__[A-Z]+__"
    if re.search(pattern, text):
        print("render_profile: unreplaced placeholder", file=sys.stderr)
        return 2

    # Write to output file with mode 0600
    out_path = Path(args.out)
    # Remove if exists then write
    out_path.unlink(missing_ok=True)
    fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)

    return 0


if __name__ == "__main__":
    sys.exit(main())
