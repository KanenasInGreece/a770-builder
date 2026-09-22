# Installing a770-builder on a new host (moving the Arc A770)

This is the move checklist for bringing the Arc A770 up on a new machine. The repository is public; the
machine-specific values (PCI BDF, group ids, image digest, keys) are gitignored and travel on the SSD, not in git.

## What the new host needs

- **Linux** with a kernel new enough for the Intel Xe/i915 driver (6.8+). The A770 is a DG2 / Xe1 card.
- **A container runtime**: `docker` (with the compose plugin) or `podman`. The SYCL image is linux/amd64.
- **Host tools**: `git`, `curl`, `python3`, `bubblewrap` (`bwrap`), `socat`, `uv`, and **`opencode` on `PATH`**
  (a private `~/.opencode/bin/opencode` is not on the default PATH until that directory is added; `doctor` is
  the check). The seat runs offline in a bubblewrap sandbox and needs these on `PATH`.
- **The account that runs the skill** must be in `docker` (or the equivalent for Podman) and `render` (usually
  `video` as well), and must be able to open `/dev/dri/renderD*`. A second login on the same host does not inherit
  those groups. The skill directory is that agent's own (`~/.claude/skills/local-build`, `~/.openclaw/skills/local-build`,
  …); do not copy a `~/.claude/...` path from SKILL.md if the copy lives elsewhere.
- **GPU drivers**: `mesa-vulkan-drivers` + `vulkan-loader` for the Vulkan path (the SYCL path's oneAPI / Level
  Zero runtime lives inside the image).
- **The kit toolchain** inside the sandbox: `node`, `cmake`, `make`, `g++` — the profiling kit's graders build and
  test against these; `local-build.sh doctor` and `tests/kit_selftest.sh` check they are reachable.

## 1. Clone and install the skill

```bash
git clone https://github.com/KanenasInGreece/a770-builder.git ~/local-ai/A770_Builder
cd ~/local-ai/A770_Builder
bash sync_local_build.sh          # install/refresh the skill copies in each agent home
bash sync_local_build.sh --check  # must say every copy is up to date
```

## 2. Configure the card (host-specific)

Find the A770's PCI address and DRM nodes — do not assume `card0` / `renderD128`, those names drift:

```bash
lspci -nn | grep -i vga            # the A770 is Intel DG2, vendor:device 8086:56a0
ls -l /dev/dri/by-path/            # the card and render nodes, by PCI BDF
getent group render; getent group video   # the host group ids the container needs
```

Set the mode and cap in the builder config (`~/.config/a770-builder/builder.env`, mode `inference` when the card
draws no desktop; `builder.display.env` for display mode). This file is gitignored; it comes from the SSD.

## 3. The gitignored config files

Copy these from the SSD into place (they are host-specific and never committed):

| From the SSD | To |
|---|---|
| `agent-config/builder.env` | `~/.config/a770-builder/builder.env` |
| `agent-config/builder.display.env` | `~/.config/a770-builder/builder.display.env` |
| `agent-config/api.key` | `~/.config/a770-builder/api.key` (mode 600) |
| `agent-config/hf.token` | `~/.config/a770-builder/hf.token` (mode 600) |
| `compose/a770-vulkan.env` | `<checkout>/compose/a770-vulkan.env` (gitignored) |
| `compose/a770-sycl.env` | `<checkout>/compose/a770-sycl.env` (gitignored) |

The tracked `compose/a770-*.env.example` files document every value. If the SSD copies are missing, copy the
examples and edit the two DRM nodes and the two group ids for THIS host.

## 4. Images (pin by digest)

```bash
docker pull ghcr.io/ggml-org/llama.cpp@sha256:33ed2a8936e94ac893a50eed3d2958141d352198233d107e58c1c7ee3b96a9b4   # Vulkan
docker pull ghcr.io/ggml-org/llama.cpp:full-intel                                                              # SYCL
docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/ggml-org/llama.cpp:full-intel
# paste the full-intel repo@sha256:… into A770B_LLAMA_IMAGE in compose/a770-sycl.env
```

Use `full-*`, never `server-*`: the speed rung runs a one-shot `llama-bench` in the same image, which `server-*`
does not ship.

## 5. Bring the server up

Host process (Vulkan, the default path):

```bash
bash skills/local-build/scripts/local-build.sh serve <profile>
```

Containerised Vulkan:

```bash
A770B_SERVE=compose A770B_COMPOSE_FILE=compose/a770-vulkan.yaml \
  bash skills/local-build/scripts/local-build.sh serve <profile>
```

Containerised SYCL:

```bash
A770B_SERVE=compose A770B_COMPOSE_FILE=compose/a770-sycl.yaml \
  bash skills/local-build/scripts/local-build.sh serve <profile>
```

`A770B_COMPOSE_FILE` is what selects the backend envelope; `A770B_SERVE=compose` makes `serve` use
`harness/serve_compose.sh`. A bare `docker compose up` is NOT the start path — it skips the profile argv, the
budget gate, the VRAM cap and the live-backend sidecar.

## 6. Verify

```bash
bash skills/local-build/scripts/local-build.sh doctor     # all checks pass (inference mode: the card draws nothing)
bash skills/local-build/scripts/local-build.sh status
curl -s 127.0.0.1:7890/health                             # {"status":"ok"} once loaded
```

## Card notes for the A770 (DG2 / Xe1)

- **Vulkan and SYCL both work** on this card for llama.cpp. **vLLM's XPU line does NOT** (its post-0.9.1 kernels
  are Xe2-only and refuse DG2) — that is the B580/B70 path, not this card's.
- **SYCL measured faster than Vulkan** for the 27B on the A770: ~10.9 vs ~8.7 tok/s decode, and on a higher-
  precision q8_0 KV cache. Prefer the SYCL envelope when serving the 27B.
- **MTP (multi-token prediction) is a net loss on this card on both backends** (Vulkan 8.7→7.0, SYCL 10.9→9.5
  tok/s), even though the draft accepts well. The `-mtp` GGUF earns nothing here; use the base build.
- One GPU process at a time: stop the host server before starting a container, and vice versa.
