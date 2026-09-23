"""No new code identifier carries a card's name.

The project serves any Intel Arc card, so a function, variable or test named after one card (the A770) is debt a later
rename has to pay. The names below existed before the per-card registry and stay until that rename; a new one fails
this test. Upper-case `A770B_*` environment variables are the existing namespace and are not listed, and the bare
card id `a770` is data (a card id in cards.json), not an identifier.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ALLOWED = {
    "A770_Builder",
    "_a770b_card_lines", "_a770b_cards_file", "_a770b_doctor_input", "_a770b_load", "_a770b_profile_lines",
    "_a770b_required_input", "_a770b_row_gpu", "_a770b_row_oneapi", "_a770b_row_vk", "_a770b_row_vram",
    "_a770b_selected_mode", "_a770b_snapshot",
    "a770b", "a770b_api_key", "a770b_data", "a770b_ensure_corpus_file", "a770b_is_profile", "a770b_model_path",
    "a770b_opencode_bin", "a770b_profile_var", "a770b_render_profile",
    "nA770B_VRAM_CAP_GIB",
    "test_profile_key_checks_against_a770b_profiles_env",
}

IDENT = re.compile(r"\b\w*a770\w*\b", re.I)


def _identifiers() -> dict[str, str]:
    found = {}
    for top in ("harness", "skills", "tests"):
        for path in (ROOT / top).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            for name in IDENT.findall(path.read_text(encoding="utf-8", errors="replace")):
                if name.startswith("A770B_") or name.lower() == "a770":
                    continue
                found.setdefault(name, str(path.relative_to(ROOT)))
    return found


def test_no_new_identifier_names_a_card():
    new = {name: where for name, where in _identifiers().items() if name not in ALLOWED}
    assert not new, f"new identifiers naming a card (use a card-neutral name): {new}"
