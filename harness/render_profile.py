#!/usr/bin/env python3
"""A Python renderer for the opencode profile, replacing the sed, with three new placeholders at their defaults.

Also validates and applies a run specification (JSON): its card becomes the agent prompt, its edit scope becomes
the edit permission rules, its bash_allow patterns are spliced in ahead of the harness's own floor of denials, and
(with --echo) a record of what was resolved is written alongside the rendered profile.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path


# The default prompt text (as specified in the brief)
DEFAULT_PROMPT = (
    "You are a coding agent working inside a git worktree of a Python project. Use the tools to read, search (grep/glob), "
    "edit and run commands. Read the brief you are pointed at, locate code with grep before reading whole files, "
    "make the change, run the exact test command given, and report the result. Be concise; do not narrate plans."
)

ALLOWED_TOP_KEYS = {"profile", "timeout", "card", "scope", "bash_allow", "context", "verify"}
ALLOWED_PROFILES = {"fast", "serious", "long"}
SCOPE_EDIT_RE = re.compile(r"^[A-Za-z0-9._/*-]+$")
BASH_ALLOW_RE = re.compile(r"^[A-Za-z0-9 ._/*:-]+$")
BASH_ALLOW_FORBIDDEN_FIRST = {
    "git", "bash", "sh", "dash", "zsh", "env", "xargs", "nohup", "sudo", "su", "rm", "uv", "pip",
    "curl", "wget", "ssh", "scp", "nc", "docker", "podman", "systemctl",
    # wrappers that run another command: the first word must be the command itself, not a way to reach one
    "timeout", "exec", "command", "busybox", "nice", "ionice", "setsid", "time", "watch", "chroot", "flatpak",
    "script", "eval", "source", "doas", "unshare", "nsenter", "strace", "ltrace", "gdb", "perl", "ruby", "node",
}
VERIFY_HIDDEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class SpecError(Exception):
    """Raised when a run specification fails validation. Carries the offending key and a human reason."""

    def __init__(self, key: str, reason: str):
        self.key = key
        self.reason = reason
        super().__init__(f"{key}: {reason}")


def _validate_card(card, seat_dir: str):
    """Return (card_text, card_source) for a validated `card` value, or raise SpecError("card", ...)."""
    if isinstance(card, str):
        if seat_dir is None:
            raise SpecError("card", "a path card requires --seat")
        seat_real = os.path.realpath(seat_dir)
        joined = os.path.join(seat_dir, card)
        joined_real = os.path.realpath(joined)
        if not joined_real.startswith(seat_real + os.sep):
            raise SpecError("card", "must stay inside --seat")
        if "/.git/" in joined_real or joined_real.endswith("/.git"):
            raise SpecError("card", "must not be inside .git")
        if not os.path.isfile(joined_real):
            raise SpecError("card", "must be a regular file")
        if os.path.islink(joined):
            raise SpecError("card", "must not be a symlink")
        try:
            card_text = Path(joined_real).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise SpecError("card", "must be readable UTF-8 text")
        card_source = card
    elif isinstance(card, dict):
        if set(card.keys()) != {"text"} or not isinstance(card.get("text"), str):
            raise SpecError("card", "must be an object with exactly one key 'text' (a string)")
        card_text = card["text"]
        card_source = "inline"
    else:
        raise SpecError("card", "must be a string path or an object with key 'text'")

    if len(card_text) > 8000:
        raise SpecError("card", "text must be at most 8000 characters")

    return card_text, card_source


def validate_and_load(spec_path: str, seat_dir: str):
    """Validate the run specification at `spec_path` against `seat_dir`.

    Returns (spec, card_text, card_source) where `spec` is the parsed JSON object, and card_text/card_source are
    None when no card is present. Raises SpecError on the first rule violated, in the order given in the brief.
    """
    # Rule 1: a regular file, not a symlink, at most 65536 bytes.
    if os.path.islink(spec_path):
        raise SpecError("spec", "must not be a symlink")
    if not os.path.isfile(spec_path):
        raise SpecError("spec", "must be a regular file")
    if os.path.getsize(spec_path) > 65536:
        raise SpecError("spec", "must be at most 65536 bytes")

    # Rule 2: parses as JSON and is an object.
    try:
        text = Path(spec_path).read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise SpecError("spec", "must be valid JSON")
    if not isinstance(data, dict):
        raise SpecError("spec", "must be a JSON object")

    # Rule 3: allowed top-level keys, exactly.
    for key in data.keys():
        if key not in ALLOWED_TOP_KEYS:
            raise SpecError(key, "unknown key")

    # Rule 4: profile / timeout.
    if "profile" in data:
        if data["profile"] not in ALLOWED_PROFILES:
            raise SpecError("profile", "must be one of fast, serious, long")
    if "timeout" in data:
        t = data["timeout"]
        if isinstance(t, bool) or not isinstance(t, int) or not (1 <= t <= 86400):
            raise SpecError("timeout", "must be an integer 1..86400")

    # Rule 5: card.
    card_text = None
    card_source = None
    if "card" in data:
        card_text, card_source = _validate_card(data["card"], seat_dir)

    # Rule 6: scope.
    if "scope" in data:
        scope = data["scope"]
        if not isinstance(scope, dict) or set(scope.keys()) != {"edit"}:
            raise SpecError("scope.edit", "scope must be an object with exactly one key 'edit'")
        edit = scope["edit"]
        if not isinstance(edit, list) or not edit or len(edit) > 50:
            raise SpecError("scope.edit", "must be a non-empty list of at most 50 strings")
        for g in edit:
            if not isinstance(g, str) or not SCOPE_EDIT_RE.match(g) or ".." in g:
                raise SpecError("scope.edit", "each glob must match ^[A-Za-z0-9._/*-]+$ and not contain '..'")

    # Rule 7: bash_allow.
    if "bash_allow" in data:
        bash_allow = data["bash_allow"]
        if not isinstance(bash_allow, list) or len(bash_allow) > 20:
            raise SpecError("bash_allow", "must be a list of at most 20 strings")
        for p in bash_allow:
            if not isinstance(p, str) or len(p) > 80 or not BASH_ALLOW_RE.match(p):
                raise SpecError("bash_allow", "each pattern must match ^[A-Za-z0-9 ._/*:-]+$, at most 80 chars")
            if p == "*":
                raise SpecError("bash_allow", "must not be '*'")
            if p != p.strip() or "  " in p:
                raise SpecError("bash_allow", "no leading, trailing or doubled spaces")
            first_word = p.split(" ")[0].lower()
            if not first_word or "/" in first_word or "*" in first_word:
                raise SpecError("bash_allow", "the first word must be a bare command name: no path, no wildcard")
            if first_word in BASH_ALLOW_FORBIDDEN_FIRST:
                raise SpecError("bash_allow", f"must not start with '{first_word}'")

    # Rule 8: context.
    if "context" in data:
        context = data["context"]
        if not isinstance(context, dict) or set(context.keys()) != {"definitions_of"}:
            raise SpecError("context.definitions_of", "context must be an object with exactly one key 'definitions_of'")
        defs = context["definitions_of"]
        if (
            not isinstance(defs, list)
            or not (1 <= len(defs) <= 8)
            or not all(isinstance(x, str) for x in defs)
        ):
            raise SpecError("context.definitions_of", "must be a list of 1 to 8 strings")
        _validate_context_definitions(seat_dir, defs)

    # Rule 9: verify.
    if "verify" in data:
        verify = data["verify"]
        if not isinstance(verify, dict) or not set(verify.keys()) <= {"test", "hidden"}:
            raise SpecError("verify", "must be an object with keys a subset of 'test', 'hidden'")
        if "test" in verify and not isinstance(verify["test"], str):
            raise SpecError("verify", "'test' must be a string")
        if "hidden" in verify:
            hidden = verify["hidden"]
            if not isinstance(hidden, list) or len(hidden) > 10:
                raise SpecError("verify", "'hidden' must be a list of at most 10 strings")
            for h in hidden:
                if not isinstance(h, str) or not VERIFY_HIDDEN_RE.match(h):
                    raise SpecError("verify", "each 'hidden' entry must match ^[A-Za-z0-9][A-Za-z0-9._-]*$")

    return data, card_text, card_source


def _write_file(path: str, content: bytes) -> None:
    """Write `content` to `path` with mode 0600, replacing any existing file (same open flags throughout)."""
    Path(path).unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, content)
    finally:
        os.close(fd)


def _build_echo(spec: dict, card_text, card_source, apikey: str, rendered_text: str, profile_name: str) -> dict:
    card_field = None
    if card_text is not None:
        card_field = {
            "source": card_source,
            "sha256": hashlib.sha256(card_text.encode("utf-8")).hexdigest(),
            "chars": len(card_text),
        }

    scope = spec.get("scope")
    scope_edit = scope["edit"] if scope else None
    bash_allow = spec.get("bash_allow") or []
    verify = spec.get("verify") or {}

    redacted = rendered_text.replace(apikey, "REDACTED") if apikey else rendered_text
    rendered_sha256 = hashlib.sha256(redacted.encode("utf-8")).hexdigest()

    return {
        "card": card_field,
        "scope_edit": scope_edit,
        "bash_allow": bash_allow,
        "profile": profile_name,
        "timeout_requested": spec.get("timeout"),
        "verify_test": verify.get("test"),
        "hidden": verify.get("hidden") or [],
        "rendered_sha256": rendered_sha256,
        "context": [],
    }


def cmd_check(args) -> int:
    try:
        validate_and_load(args.spec, args.seat)
    except SpecError as e:
        print(f"render_profile: {e.key}: {e.reason}", file=sys.stderr)
        return 2
    return 0


def _validate_context_definitions(seat_dir: str, definitions_of: list[str]) -> None:
    """Validate the context definitions paths. Raises SpecError on failure."""
    seat_real = os.path.realpath(seat_dir)
    for p in definitions_of:
        joined = os.path.join(seat_dir, p)
        joined_real = os.path.realpath(joined)
        if not joined_real.startswith(seat_real + os.sep):
            raise SpecError("context.definitions_of", "path must be inside --seat")
        if "/.git/" in joined_real or joined_real.endswith("/.git"):
            raise SpecError("context.definitions_of", "path must not be inside .git")
        if not os.path.isfile(joined_real):
            raise SpecError("context.definitions_of", "path must be a regular file")
        if os.path.islink(joined):
            raise SpecError("context.definitions_of", "path must not be a symlink")


CONTEXT_DEF_RE = re.compile(r"^\s*(def |class |async def |function |[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{)")

CONTEXT_PER_FILE_CAP = 400
CONTEXT_TOTAL_CAP = 1200


def cmd_context(args) -> int:
    try:
        spec, card_text, card_source = validate_and_load(args.spec, args.seat)
    except SpecError as e:
        print(f"render_profile: {e.key}: {e.reason}", file=sys.stderr)
        return 2

    if not os.path.isfile(args.brief_copy):
        print("render_profile: brief-copy: must be a regular file", file=sys.stderr)
        return 2

    context_spec = spec.get("context")
    if context_spec is None:
        # No context key: do nothing to the brief; if an echo file already exists, set its
        # context key to [] and rewrite it. Nothing is written when no echo path is given, and
        # nothing is created when an echo path is given but no file exists there yet.
        if args.echo:
            echo_path = Path(args.echo)
            if echo_path.exists():
                echo_data = json.loads(echo_path.read_text(encoding="utf-8"))
                echo_data["context"] = []
                _write_file(args.echo, json.dumps(echo_data).encode("utf-8"))
        return 0

    definitions_of = context_spec["definitions_of"]

    # Read each named file exactly once, keeping the definition-like lines up to the per-file
    # cap and the running total cap (once the total is reached, no further lines are kept from
    # any later file, or from later lines of the current file).
    per_file = []
    total_kept = 0
    for p in definitions_of:
        file_path = os.path.join(args.seat, p)
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
        kept = []
        matching_count = 0
        for lineno, line in enumerate(content.splitlines(), start=1):
            if CONTEXT_DEF_RE.match(line):
                matching_count += 1
                if len(kept) < CONTEXT_PER_FILE_CAP and total_kept < CONTEXT_TOTAL_CAP:
                    kept.append((lineno, line.rstrip()))
                    total_kept += 1
        per_file.append((p, kept, matching_count))

    # Build the definitions section, grouped per file: under each "### <p>" heading only that
    # file's own kept lines appear.
    lines_out = ["", "## Definitions in the named files (prepared by the harness, not by the model)"]
    for p, kept, matching_count in per_file:
        lines_out.append("")
        lines_out.append(f"### {p}")
        for lineno, line in kept:
            lines_out.append(f"{lineno}: {line}")
        if matching_count > len(kept):
            lines_out.append(f"(truncated at {len(kept)} lines)")

    with open(args.brief_copy, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(lines_out))

    echo_data = {}
    if args.echo:
        echo_path = Path(args.echo)
        if echo_path.exists():
            echo_data = json.loads(echo_path.read_text(encoding="utf-8"))
    echo_data["context"] = [{"path": p, "lines": len(kept)} for p, kept, _ in per_file]

    if args.echo:
        _write_file(args.echo, json.dumps(echo_data).encode("utf-8"))

    return 0


def cmd_render(args) -> int:
    spec = {}
    card_text = None
    card_source = None

    if args.spec is not None:
        if args.seat is None:
            print("render_profile: seat: --spec requires --seat", file=sys.stderr)
            return 2
        try:
            spec, card_text, card_source = validate_and_load(args.spec, args.seat)
        except SpecError as e:
            print(f"render_profile: {e.key}: {e.reason}", file=sys.stderr)
            return 2

    # Read the template file as UTF-8 text
    template_path = Path(args.template)
    text = template_path.read_text(encoding="utf-8")

    # Replace placeholders with given values
    text = text.replace("__BASEURL__", args.baseurl)
    text = text.replace("__APIKEY__", args.apikey)
    text = text.replace("__CTX__", args.ctx)
    text = text.replace("__OUTPUT__", args.output)
    text = text.replace("__NAME__", args.name)

    # Replace __PROMPT__ with the card text when present, DEFAULT_PROMPT otherwise; JSON-escaped, no quotes.
    prompt_value = card_text if card_text is not None else DEFAULT_PROMPT
    json_escaped_prompt = json.dumps(prompt_value)[1:-1]
    text = text.replace("__PROMPT__", json_escaped_prompt)

    # Replace __EDIT_RULES__ with the deny-then-allow splice when scope.edit is present, else "*": "allow".
    scope = spec.get("scope")
    if scope:
        globs = scope["edit"]
        allows = ", ".join(f"{json.dumps(g)}: \"allow\"" for g in globs)
        edit_rules = f'"*": "deny", {allows}'
    else:
        edit_rules = '"*": "allow"'
    text = text.replace("__EDIT_RULES__", edit_rules)

    # Replace __BASH_ALLOW__ with the given patterns spliced ahead of the floor, else empty.
    bash_allow = spec.get("bash_allow") or []
    bash_allow_str = "".join(f'{json.dumps(p)}: "allow", ' for p in bash_allow)
    text = text.replace("__BASH_ALLOW__", bash_allow_str)

    # Check for any remaining unreplaced placeholders
    pattern = r"__[A-Z][A-Z_]*__"
    if re.search(pattern, text):
        print("render_profile: unreplaced placeholder", file=sys.stderr)
        return 2

    # Write to output file with mode 0600
    _write_file(args.out, text.encode("utf-8"))

    if args.echo:
        echo_data = _build_echo(spec, card_text, card_source, args.apikey, text, args.name)
        _write_file(args.echo, json.dumps(echo_data).encode("utf-8"))

    return 0


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
    render_parser.add_argument("--spec", help="Path to a run specification JSON (requires --seat)")
    render_parser.add_argument("--seat", help="Path to the seat directory (required with --spec)")
    render_parser.add_argument("--echo", help="Path to write a JSON echo of the resolved specification")

    check_parser = subparsers.add_parser("check", help="Validate a run specification")
    check_parser.add_argument("--spec", required=True, help="Path to a run specification JSON")
    check_parser.add_argument("--seat", required=True, help="Path to the seat directory")

    context_parser = subparsers.add_parser("context", help="Apply context definitions to a brief")
    context_parser.add_argument("--spec", required=True, help="Path to a run specification JSON")
    context_parser.add_argument("--seat", required=True, help="Path to the seat directory")
    context_parser.add_argument("--brief-copy", required=True, help="Path to the brief file to append definitions to")
    context_parser.add_argument("--echo", help="Path to write the echo JSON")

    args = parser.parse_args()

    if args.command == "render":
        return cmd_render(args)
    elif args.command == "check":
        return cmd_check(args)
    elif args.command == "context":
        return cmd_context(args)

    return 2


if __name__ == "__main__":
    sys.exit(main())
