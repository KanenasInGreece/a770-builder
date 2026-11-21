# Strata API fixtures

Stand-ins for an attached Strata v0.2.16 endpoint, written to the response contract of its
`terminal/serve/server.py` `/health` and `/v1/models` routes as the slice brief states it:

- `/health`: `service` (`"strata"`), `loaded`, `model`, `max_context`.
- `/v1/models`: text and image rows, a nested `status.value`, `meta.n_ctx`, alias rows (`alias_of`), and
  `architecture` with `input_modalities` / `output_modalities`. A text row names `"text"` in
  `output_modalities`; an image row names `["image"]` and may carry no `meta`.

They are hand-written to that contract, **not** captured from a live server.
`strata_attach.py probe --evidence F` records the response *shapes* (keys and types, no text) of a live endpoint.

- `health.json`: a loaded Strata, `max_context` 32768. `health-unloaded.json`: no model loaded.
  `health-small-window.json`: `max_context` 8192, below the row's `meta.n_ctx`.
- `models.json`: an image-only row first (no `meta`), an unloaded text row, the requested text row, and an alias of it.
- `models-unloaded.json`: the requested row with `status.value` `unloaded`. `models-missing-meta.json`: no
  `meta`, so no window. `models-flat-status.json`: a flat `"status": "loaded"`, which is not the pinned shape.
- `tool-call.sse`: a streamed `read_file` call on `NOTE.txt`, arguments split across deltas, a usage chunk and
  `[DONE]`. The fake endpoint swaps `call_0` for a fresh id per run. `tool-call-no-id.sse`: the same call with no id.
- `followup.sse`: an answer with no usage chunk. The usage tap records it as unmeasured, not zero.
