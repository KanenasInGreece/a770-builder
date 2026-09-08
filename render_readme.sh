#!/usr/bin/env bash
# render_readme.sh — generates the OPERATOR'S LOCAL RECAP, README.html: a short page for returning to this project
#   after time away. It is not the public documentation. README.md is the document of record and is written and
#   reviewed FIRST, at every change; this script only reads the repository's current state (README.md, the profile
#   registries, VERSION, the skill's usage line, the suite runner's header) afterwards and never edits any of it.
#   README.html is gitignored and never shipped.
#   bash render_readme.sh          → writes README.html beside README.md
set -euo pipefail
cd "$(dirname "$0")"
python3 - <<'PY'
import json, re, subprocess, html as _html, pathlib

root = pathlib.Path(".")


def esc(s):
    return _html.escape(s, quote=False)


def inline(s):
    """Escape then render `code` spans."""
    s = esc(s)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)


# ---------- 1. what the project is, in three sentences (pulled from README.md's opening paragraph) ----------
readme = pathlib.Path("README.md").read_text()
title_match = re.search(r"^#\s+(.+)$", readme, re.M)
project = title_match.group(1).strip() if title_match else "this project"

body_after_title = readme[title_match.end():] if title_match else readme
first_para = body_after_title.strip().split("\n\n", 1)[0]
first_para = " ".join(line.strip() for line in first_para.splitlines())
sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", first_para)
intro = " ".join(sentences[:3])

# ---------- 2. the two card modes and the profiles each serves ----------
def load_profiles(path):
    data = json.loads(pathlib.Path(path).read_text())
    return data["mode"], data["profiles"]

rows = []
for cfg in ("config/profiles.json", "config/profiles.inference.json"):
    mode, profiles = load_profiles(cfg)
    for name, p in profiles.items():
        ctx = p.get("ctx")
        useful = p.get("useful_ctx")
        window = f"{ctx:,} (~{useful:,})" if ctx and useful else "—"
        vram = p.get("vram_gib_after_load")
        vram_s = f"{vram} GiB" if vram is not None else "—"
        dec = p.get("speed", {}).get("decode_tps", {}).get("8k")
        pre = p.get("speed", {}).get("prefill_tps", {}).get("8k")
        speed = f"{dec} / {pre} tok/s" if dec is not None and pre is not None else "—"
        instrument = p.get("instrument", "—")
        rows.append((mode, name, p.get("model", "—"), window, vram_s, speed, instrument))

table_html = ['<table>', '<thead><tr><th>mode</th><th>profile</th><th>model</th>'
              '<th>window (useful)</th><th>VRAM</th><th>decode / prefill @8k</th><th>instrument</th></tr></thead>',
              '<tbody>']
for mode, name, model, window, vram_s, speed, instrument in rows:
    table_html.append(
        f"<tr><td>{esc(mode)}</td><td>{esc(name)}</td><td>{esc(model)}</td>"
        f"<td>{esc(window)}</td><td>{esc(vram_s)}</td><td>{esc(speed)}</td><td>{esc(instrument)}</td></tr>"
    )
table_html.append("</tbody></table>")
table_html = "\n".join(table_html)

# ---------- 3. how to run the seat and the suite (taken verbatim, not invented) ----------
skill_md = pathlib.Path("skills/local-build/SKILL.md").read_text()
cmd_lines = re.findall(r"^bash .*local-build\.sh \S+.*$", skill_md, re.M)

def pick(cmds, needle, avoid=()):
    for c in cmds:
        if needle in c and not any(a in c for a in avoid):
            return c.split("#")[0].strip()
    return None

run_cmd = pick(cmd_lines, "run <brief.md> --profile long", avoid=("--spec",))
verify_cmd = pick(cmd_lines, "verify <label>")
serve_cmd = pick(cmd_lines, "serve <profile>")

run_suite_sh = pathlib.Path("harness/run_suite.sh").read_text()
m = re.search(r'usage:\s*(run_suite\.sh[^"\n]*)', run_suite_sh)
suite_cmd = ("bash harness/run_suite.sh " + m.group(1).split(None, 1)[1]) if m else None

commands = [c for c in (run_cmd, verify_cmd, serve_cmd, suite_cmd) if c]

# ---------- 4. where things live (six lines, one clause each) ----------
where = [
    ("the kit", "<code>kit/</code> — the profiling suite's own seat, tasks and hidden graders"),
    ("the registries", "<code>config/profiles.json</code>, <code>config/profiles.inference.json</code> — the two card-mode profile tables"),
    ("the ledger", "<code>config/models.md</code> — every model measured on this card, and why"),
    ("the guide", "<code>docs/OPERATING.md</code> — day-to-day running of the seat, every knob"),
    ("the results directory", "<code>$A770B_DATA/results</code> (default <code>~/local-ai/results</code>) — each run's capture"),
    ("the weights directory", "<code>$A770B_MODELS</code> (default <code>~/LLM/tested</code>) — the GGUF files the registries name"),
]

# ---------- 5. the version and the release this checkout stands on ----------
version = pathlib.Path("VERSION").read_text().strip() if pathlib.Path("VERSION").exists() else "—"
try:
    describe = subprocess.run(["git", "describe", "--tags"], capture_output=True, text=True, check=True).stdout.strip()
except Exception:
    describe = "—"

# ---------- 6. what is open (only if a machine-readable source exists; else omitted) ----------
open_items_html = ""
open_items_path = root / "OPEN_ITEMS.json"
if open_items_path.exists():
    items = json.loads(open_items_path.read_text())
    if items:
        li = "\n".join(f"<li>{inline(str(i))}</li>" for i in items)
        open_items_html = f"<h2>What is open</h2>\n<ul>\n{li}\n</ul>\n"

# ---------- assemble ----------
css = """
:root{--ground:#F6F7F9;--surface:#FFFFFF;--surface-2:#EEF1F5;--ink:#141922;--ink-2:#4A5361;--ink-3:#6E7785;--rule:#D6DBE3;--accent:#0B5FBF;--code-bg:#1B222D;--code-ink:#E7ECF3}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--ground:#0F141B;--surface:#161C25;--surface-2:#1E2631;--ink:#E6EAF0;--ink-2:#AEB7C4;--ink-3:#8791A0;--rule:#2A3441;--accent:#5AA9FF;--code-bg:#0B1017;--code-ink:#DCE3EC}}
:root[data-theme="dark"]{--ground:#0F141B;--surface:#161C25;--surface-2:#1E2631;--ink:#E6EAF0;--ink-2:#AEB7C4;--ink-3:#8791A0;--rule:#2A3441;--accent:#5AA9FF;--code-bg:#0B1017;--code-ink:#DCE3EC}
*{box-sizing:border-box}body{margin:0;background:var(--ground);color:var(--ink);font-family:"Source Sans 3",system-ui,-apple-system,"Segoe UI",sans-serif;font-size:16px;line-height:1.55}
.wrap{max-width:72rem;margin:0 auto;padding:2.5rem 1.5rem 4rem}
h1,h2,h3{font-family:"Barlow Semi Condensed","Arial Narrow",sans-serif;text-wrap:balance;line-height:1.1}
h1{font-size:2.6rem;font-weight:700;letter-spacing:-.01em;margin:0 0 .5rem}h2{font-size:1.45rem;font-weight:700;margin:2.75rem 0 .5rem;padding-top:1.1rem;border-top:2px solid var(--ink)}h3{font-size:1.1rem;font-weight:600;margin:1.5rem 0 .4rem}
p,li{max-width:72ch}p{margin:.75rem 0}ul,ol{padding-left:1.3rem}li{margin:.3rem 0}
a{color:var(--accent)}a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
code{font-family:"JetBrains Mono",monospace;font-size:.88em;background:var(--surface-2);padding:.05em .35em;border-radius:2px}
pre{background:var(--code-bg);color:var(--code-ink);font-family:"JetBrains Mono",monospace;font-size:.8rem;line-height:1.5;padding:1rem 1.1rem;overflow-x:auto;margin:.75rem 0;border-left:3px solid var(--accent)}pre code{background:none;padding:0;font-size:inherit;color:inherit}
table{border-collapse:collapse;width:100%;font-size:.85rem;font-variant-numeric:tabular-nums;display:block;overflow-x:auto;border:1px solid var(--rule);background:var(--surface);margin:1rem 0}
th,td{padding:.55rem .7rem;text-align:left;vertical-align:top;border-bottom:1px solid var(--rule)}
th{font-family:"JetBrains Mono",monospace;font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);background:var(--surface-2);white-space:nowrap}
tr:last-child td{border-bottom:none}blockquote{border-left:3px solid var(--rule);margin:1rem 0;padding:.2rem 1rem;color:var(--ink-2)}
.note{color:var(--ink-2);font-size:.9rem}
@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
"""

commands_html = "\n".join(inline(c) for c in commands)

parts = []
parts.append(f"<h1>{esc(project)} — operator recap</h1>")
parts.append(
    '<p class="note">Local recap, generated from the repository\'s own state — not shipped, not the document of '
    f"record. <code>README.md</code> is the document of record for anyone but the operator.</p>"
)
parts.append(f"<p>{inline(intro)}</p>")
parts.append("<h2>Card modes and profiles</h2>")
parts.append(table_html)
parts.append("<h2>Run it</h2>")
parts.append(f"<pre><code>{commands_html}</code></pre>")
parts.append("<h2>Where things live</h2>")
parts.append("<ul>\n" + "\n".join(f"<li><strong>{esc(k)}:</strong> {v}</li>" for k, v in where) + "\n</ul>")
parts.append("<h2>Version</h2>")
parts.append(f"<p>{esc(version)} — checkout at <code>{esc(describe)}</code>.</p>")
if open_items_html:
    parts.append(open_items_html)

out_body = "\n".join(parts)
html = (f"<title>{esc(project)} — recap</title>\n"
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@600;700&family=Source+Sans+3:ital,wght@0,400;0,600;1,400&family=JetBrains+Mono:wght@400;600&display=swap">\n'
        f"<style>{css}</style>\n<div class=\"wrap\">\n{out_body}\n</div>\n")
pathlib.Path("README.html").write_text(html)
print(f"README.html (local recap) regenerated from the repository's current state ({len(html)} chars)")
PY
