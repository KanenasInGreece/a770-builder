#!/usr/bin/env bash
# bench_model.sh — start one GGUF on the A770 (llama.cpp Vulkan) and measure it. TESTING ONLY.
#   bash $A770B_DATA/A770_Builder/harness/bench_model.sh <label> <gguf> <ctx> [extra llama-server args…]
# Leaves the server RUNNING (for the build task) and writes $A770B_DATA/results/<label>.json with:
#   load_s, vram_gib, quality (3 greedy answers), ttft_short_ms / tpot_ms / decode_tps (short prompt),
#   ttft_long_ms at ~N tokens of repo text (long-context prefill), toolcall_ms (round trip incl. parse).
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/guard.sh"
set -euo pipefail
LABEL="${1:?label}"; GGUF="${2:?gguf}"; CTX="${3:?ctx}"; shift 3 || shift $#
OUT="$A770B_DATA/results/$LABEL.json"; LOG="$A770B_DATA/logs/llamacpp-a770.log"
mkdir -p "$A770B_DATA/results"
bash "$A770B_PROJECT/harness/serve_a770_llamacpp.sh" stop >/dev/null 2>&1 || true
t0=$(date +%s.%N)
bash "$A770B_PROJECT/harness/serve_a770_llamacpp.sh" start "$GGUF" "$CTX" "$@" | tail -1
for _ in $(seq 1 150); do curl -sf --max-time 2 $A770B_HOST:$A770B_PORT/health 2>/dev/null | grep -q '"ok"' && break; kill -0 "$(cat "$A770B_DATA/logs/llamacpp-a770.pid")" 2>/dev/null || { echo "SERVER DIED"; tail -8 "$LOG" | cut -c1-200; exit 1; }; sleep 2; done
curl -sf $A770B_HOST:$A770B_PORT/health >/dev/null || { echo "not ready"; exit 1; }
load_s=$(echo "$(date +%s.%N) - $t0" | bc)
vram=$(gpu_used_gib)
A770B_KEY=$(a770b_api_key); export LABEL GGUF CTX load_s vram OUT A770B_KEY
python3 - "$A770B_PROBE_CORPUS" <<'PY'
import json,os,sys,time,urllib.request,subprocess,glob
U=f"http://{os.environ['A770B_HOST']}:{os.environ['A770B_PORT']}"
def post(path,body,timeout=600):
    req=urllib.request.Request(U+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['A770B_KEY']})
    t=time.time(); d=json.load(urllib.request.urlopen(req,timeout=timeout)); return d,(time.time()-t)*1000
def chat(msgs,max_tokens=64,**kw):
    b={"model":"local-builder","messages":msgs,"temperature":0,"max_tokens":max_tokens}; b.update(kw); return post('/v1/chat/completions',b)
r={"label":os.environ['LABEL'],"gguf":os.environ['GGUF'],"ctx":int(os.environ['CTX']),"load_s":round(float(os.environ['load_s']),1),"vram_gib_after_load":float(os.environ['vram']),"ts":time.strftime('%Y-%m-%dT%H:%M:%S')}
q=[]
# max_tokens 256 (was 48), 4096 when REASONING=on: reasoning models (gpt-oss harmony, or this server's own --reasoning on)
# spend their budget in reasoning_content before the answer; the gate reads the CONTENT field only — a builder must deliver in content.
GATE_MAX_TOKENS=4096 if os.environ.get("REASONING","off")=="on" else 256
for p,exp in [("What is 2+2? Answer with the number only.","4"),("What is the capital of Greece? One word.","Athens"),("Write a Python one-liner that prints hello world. Code only.","print(")]:
    d,ms=chat([{"role":"user","content":p}],max_tokens=GATE_MAX_TOKENS); c=(d['choices'][0]['message'].get('content') or '').strip(); q.append({"prompt":p,"answer":c[:120],"ok":exp.lower() in c.lower(),"ms":round(ms),"reasoning_chars":len(d['choices'][0]['message'].get('reasoning_content') or '')})
r["quality"]=q; r["quality_ok"]=all(x['ok'] for x in q)
d,ms=chat([{"role":"user","content":"Write a 200-word paragraph about git worktrees."}],max_tokens=256)
t=d.get('timings',{}); r["short"]={"prompt_tokens":t.get('prompt_n'),"ttft_ms":round(t.get('prompt_ms',0)),"gen_tokens":t.get('predicted_n'),"tpot_ms":round(t.get('predicted_per_token_ms',0),2),"decode_tps":round(t.get('predicted_per_second',0),1),"wall_ms":round(ms)}
files=sorted(glob.glob(os.path.expanduser(sys.argv[1]),recursive=True))
text=""
for f in files:
    text+=f"\n\n### FILE {os.path.basename(f)}\n"+open(f,errors='ignore').read()
    if len(text)>40000: break   # ≈32k tokens; a 74k-token prefill on the display card reset the Xe engine (2026-09-06 21:05)
d,ms=chat([{"role":"user","content":"Here is source code from a project:\n"+text+"\n\nIn ONE sentence, what does this project do?"}],max_tokens=256,timeout=1800)
t=d.get('timings',{}); r["long"]={"prompt_tokens":t.get('prompt_n'),"ttft_ms":round(t.get('prompt_ms',0)),"prefill_tps":round(t.get('prompt_per_second',0),1),"tpot_ms_after_long":round(t.get('predicted_per_token_ms',0),2),"answer":(d['choices'][0]['message'].get('content') or '')[:160],"wall_ms":round(ms)}
tools=[{"type":"function","function":{"name":"read_file","description":"Read a file from the repository","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}}]
d,ms=chat([{"role":"user","content":"Read the file README.md using the tool."}],max_tokens=96,tools=tools,tool_choice="auto")
m=d['choices'][0]['message']; r["toolcall"]={"ms":round(ms),"parsed":bool(m.get('tool_calls')),"call":json.dumps(m.get('tool_calls'))[:200],"gen_tokens":d.get('timings',{}).get('predicted_n')}
r["vram_gib_after_probes"]=float(subprocess.check_output(['bash','-c',f". \"{os.environ['A770B_PROJECT']}/harness/env.sh\" >/dev/null 2>&1; . \"{os.environ['A770B_PROJECT']}/harness/guard.sh\"; gpu_used_gib"]).decode().strip())
r["kernel_resets"]=int(subprocess.check_output("journalctl -k --since @$(stat -c %Y $A770B_DATA/logs/llamacpp-a770.pid) --no-pager 2>/dev/null | grep -ciE 'engine reset|timedout' || true",shell=True).decode().strip() or 0)
json.dump(r,open(os.environ['OUT'],'w'),indent=1)
print(json.dumps({k:r[k] for k in ['label','load_s','vram_gib_after_load','quality_ok','short','long','toolcall','vram_gib_after_probes','kernel_resets']})[:1500])
PY
