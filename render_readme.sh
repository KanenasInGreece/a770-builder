#!/usr/bin/env bash
# render_readme.sh — README.html is GENERATED from README.md (the source GitHub renders). Run after every README edit.
#   bash render_readme.sh          → writes README.html beside README.md
set -euo pipefail
cd "$(dirname "$0")"
uv run --quiet --with markdown python - <<'PY'
import markdown, re, pathlib
md = pathlib.Path("README.md").read_text()
title = re.search(r'^#\s+(.+)$', md, re.M).group(1).strip()
body = markdown.markdown(md, extensions=["tables", "fenced_code", "toc", "sane_lists"])
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
table{border-collapse:collapse;width:100%;font-size:.9rem;font-variant-numeric:tabular-nums;display:block;overflow-x:auto;border:1px solid var(--rule);background:var(--surface);margin:1rem 0}
th,td{padding:.55rem .7rem;text-align:left;vertical-align:top;border-bottom:1px solid var(--rule)}
th{font-family:"JetBrains Mono",monospace;font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);background:var(--surface-2);white-space:nowrap}
tr:last-child td{border-bottom:none}blockquote{border-left:3px solid var(--rule);margin:1rem 0;padding:.2rem 1rem;color:var(--ink-2)}
@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
"""
html = (f"<title>{title}</title>\n"
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@600;700&family=Source+Sans+3:ital,wght@0,400;0,600;1,400&family=JetBrains+Mono:wght@400;600&display=swap">\n'
        f"<style>{css}</style>\n<div class=\"wrap\">\n{body}\n</div>\n")
pathlib.Path("README.html").write_text(html)
print(f"README.html rendered from README.md ({len(md)} chars → {len(html)} chars)")
PY
