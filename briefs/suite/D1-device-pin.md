# Task: the builder card is pinned by PCI id, not by Vulkan index — three edits in three files

You are in a standalone clone of a770-builder at commit 391e717 (main). The task is four edits in `harness/env.sh`,
`harness/serve_a770_llamacpp.sh` and `tests/selftest.sh` and nothing else. Do not create files. Do not edit any file
not named below.

## Why

Vulkan lists the boot card first. When the desktop moves to another card, Vulkan0 becomes that card and the server
would load a model beside the encoders that live there. Mesa's device selector, `MESA_VK_DEVICE_SELECT=8086:56a0!`,
makes the A770 the only Vulkan device the server can see, so `--device Vulkan0` keeps meaning the builder card whatever
boots first.

## The files

- `harness/env.sh` — read lines 27–36 only (`sed -n '27,36p' harness/env.sh`): the server knobs.
- `harness/serve_a770_llamacpp.sh` — read lines 1–8 and 25–29 only.
- `tests/selftest.sh` — read lines 108–116 only (`sed -n '108,116p' tests/selftest.sh`): the last check and the closing lines.

## The change

1. `harness/env.sh`: insert ONE new line immediately after line 29 (the line starting `: "${A770B_DEVICE:=Vulkan0}"`), exactly:
```
: "${A770B_VK_DEVICE_SELECT:=8086:56a0!}"                # Mesa device selector, vendor:device of the builder card with '!' = the only Vulkan device the server sees (A770 = 8086:56a0); Vulkan lists the boot card first, so an index alone drifts when the desktop moves
```
2. `harness/serve_a770_llamacpp.sh` line 27 starts `nohup "$A770B_LLAMA_BIN" -m "$MODEL"` → starts `MESA_VK_DEVICE_SELECT="$A770B_VK_DEVICE_SELECT" nohup "$A770B_LLAMA_BIN" -m "$MODEL"` (only that prefix is added; the rest of the line is unchanged).
3. `harness/serve_a770_llamacpp.sh` line 5: `# Env (see config/builder.env.example): A770B_DEVICE, A770B_PORT, A770B_UBATCH, A770B_VRAM_CAP_GIB; per call KV_K/KV_V` → `# Env (see config/builder.env.example): A770B_DEVICE, A770B_VK_DEVICE_SELECT, A770B_PORT, A770B_UBATCH, A770B_VRAM_CAP_GIB; per call KV_K/KV_V`
4. `tests/selftest.sh`: insert TWO lines immediately BEFORE the line that starts `rm -rf "$t"`, exactly:
```
[ "$A770B_VK_DEVICE_SELECT" = "8086:56a0!" ] && echo "ok   device: the builder card is pinned by PCI id by default" || { echo "FAIL device: A770B_VK_DEVICE_SELECT default is '$A770B_VK_DEVICE_SELECT'"; fail=1; }
grep -q '^MESA_VK_DEVICE_SELECT="\$A770B_VK_DEVICE_SELECT" nohup "\$A770B_LLAMA_BIN"' "$here/harness/serve_a770_llamacpp.sh" && echo "ok   device: the server starts under the selector" || { echo "FAIL device: serve_a770_llamacpp.sh does not export the selector to the server"; fail=1; }
```

## Verify — run exactly this and print its last two lines

```
bash tests/selftest.sh; echo EXIT=$?
```

Expected: two `ok   device:` lines among the output, then `selftest: all passed` and `EXIT=0`.

## Rules

Work only inside this directory. No version-control command that changes state. No docker, no systemctl, no network.
Keep every edit on one physical line; never re-wrap. Change nothing but the characters named in each row.

## Stop when

`grep -c 'A770B_VK_DEVICE_SELECT' harness/env.sh harness/serve_a770_llamacpp.sh tests/selftest.sh` prints 1, 2 and 2, and
the verify prints as expected. Then print the list of files you changed.
