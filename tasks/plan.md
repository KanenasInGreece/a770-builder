# Implementation Plan: cards migration — model cards replace window profiles

## Overview

Rename the window-name profile keys (`long`/`fast`/`serious`/`serious-sycl`) to card identities so the
menu presents model cards, not window names, and selection is no longer steered by speed/default alone.
Key scheme ruled by the operator: **family-weight-quant-backend**, dot-free lowercase (the key is the
`--profile` value and the `A770B_<KEY>_*` env-var stem, and `profiles.py` validates `^[a-z][a-z0-9-]*$`).
The same cycle re-measures the inference registry on the kit instrument SUITE-1@1.

## Key mapping

| old key | new key (card identity) | model | quant | backend |
|---|---|---|---|---|
| display `long` | `qwen35-9b-q4km-vulkan` | Qwen3.5-9B-Q4_K_M | Q4_K_M | vulkan |
| display `fast` | `gemma4-8b-e4b-q4km-vulkan` | gemma-4-E4B-it-Q4_K_M | Q4_K_M | vulkan |
| display `serious` | `qwen38-27b-iq3xxs-vulkan` | Qwen3.8-27B IQ3_XXS | IQ3_XXS | vulkan |
| inference `long` | `qwen35-9b-q4km-vulkan` | Qwen3.5-9B-Q4_K_M | Q4_K_M | vulkan |
| inference `serious` | `qwen38-27b-iq3s-vulkan` | Qwen3.8-27B IQ3_S | IQ3_S | vulkan |
| inference `serious-sycl` | `qwen38-27b-iq3s-sycl` | Qwen3.8-27B IQ3_S | IQ3_S | sycl |

The two `qwen35-9b-q4km-vulkan` keys live in different mode registries (separate files), so no collision.
The gemma weight segment mirrors the registry's `weight_class` (`8b-e4b`).

## Task list

### Phase 1: registry rename (schema/data)
- [ ] T1: rename the keys in `config/profiles.json` and `config/profiles.inference.json`; `profiles.py check` green.
- [ ] T2: update `harness/render_profile.py` `DEFAULT_ALLOWED_PROFILES` fallback to the new keys.

### Phase 2: menu presents cards, no default
- [ ] T3: `cmd_menu`/`cmd_card` — `_menu_row` already carries the card tuple; add `task`/`weight_class`/`quant`
      and present the card identity as the primary handle (the key is already the identity).
- [ ] T4: confirm "no default" is already enforced end-to-end (registry `default` removed in v0.2.8); verify
      `serve`/`run` require an explicit card and the operator_override card (`qwen38-27b-iq3s-sycl`) is reported.

### Phase 3: tests
- [ ] T5: update `tests/test_profiles.py`, `test_menu.py`, `test_live_backend.py`, `test_reload.py`,
      `test_serve_compose.py`, `test_kit.py`, `test_ladder_row.py`, `test_render_profile.py`,
      `test_prompt_budget.py` to the new keys (or to first-key-of-registry where the name is incidental).
- [ ] T6: `tests/selftest.sh` references.

### Phase 4: docs + skill (the two doc reviews)
- [ ] T7: SKILL.md — fix the stale `description` frontmatter (llama.cpp Vulkan, missing serious-sycl, window names);
      prune the narrative toward card-first terse instruction; re-render the tables.
- [ ] T8: AGENTS.md — prune per "agents get instructions, README is for humans" (it is already 45 lines; tighten).
- [ ] T9: README.md, config/models.md, config/README.md, docs/OPERATING.md, docs/COMPARISON.md, kit/*.md
      (the things-that-move-together set) to the new keys.

### Phase 5: re-measure (GPU campaign — measurement, not code)
- [ ] T10: run SUITE-1@1 (`harness/run_suite.sh <card>`) on each inference card, record the suite object and
      task-fit into the registry rows; expected per fact:2422 that no row clears 80% — record honestly.

## OOM guard (operator instruction)

Check `MemAvailable` before and after every `serve`/kit-stage step; pause and report if it trends
toward exhaustion. The shipped dense rows are VRAM-bound (`ram_gb_extra: 0`); the only known
host-RAM spiller is the MoE 35B, which is not shipped. Baseline 2026-09-15: 33 GiB available.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| `long`/`fast`/`serious` also appear as English prose ("long window", "the long profile") | rename the wrong thing | change only the key/CLI/env references, leave prose; grep-by-context, not blanket replace |
| `long` appears in gitignored test fixtures (`tests/data/*long*`) | churn | rename only where the fixture CONTENT keys on the profile, not filename-only |
| SUITE-1@1 re-measure yields no green row | no task score to ship | record the honest result; the migration ships cards with `suite` as measured, speed/window recorded not gated |
| Rename breaks the operator's `builder.env` (`A770B_SERIOUS_SYCL_*`) | card won't serve | document the new env-var stems; the operator_override card keeps its override under the new name |

## Open questions
- Gemma weight segment `8b-e4b` vs `e4b` (proposed `8b-e4b`, mirrors `weight_class`).
