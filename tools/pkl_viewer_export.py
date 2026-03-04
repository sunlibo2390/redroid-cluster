#!/usr/bin/env python3
"""
Usage:
    python pkl_viewer_export.py path/to/run.pkl
    python pkl_viewer_export.py path/to/run.pkl --out viewer.html
Then open viewer.html in any browser (VSCode port-forward, scp, etc.)
"""
import argparse, base64, html, io, json, os, pickle, sys
from pathlib import Path
from PIL import Image

REPO_ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).parent.parent)).resolve()

def _abs(p):
    pp = Path(p)
    return pp if pp.is_absolute() else (REPO_ROOT / pp).resolve()

def load_runs(pkl_path):
    with open(pkl_path, "rb") as f:
        payload = pickle.load(f)
    runs = []
    for tr in payload.get("task_results", []):
        sf_str = tr.get("step_records_jsonl", "").strip()
        if not sf_str:
            continue
        sf = _abs(sf_str)
        if not sf.exists() or not sf.is_file():
            continue
        steps = []
        with open(sf, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                r = json.loads(line)
                for k in ("before_raw_png", "before_som_png", "after_som_png"):
                    r[k + "_abs"] = str(_abs(r.get(k, "")))
                steps.append(r)
        steps.sort(key=lambda x: int(x.get("step_idx", 0)))
        runs.append({"label": f"{tr.get('task_name','?')} | combo {tr.get('combo_idx',0)} | {len(steps)} steps",
                     "tr": tr, "steps": steps})
    return runs

def img_b64(path_str):
    p = Path(path_str)
    if not p.exists(): return ""
    with open(p, "rb") as f: data = f.read()
    # Re-encode to JPEG only if not already JPEG, to keep file size reasonable
    try:
        im = Image.open(io.BytesIO(data))
        if im.format != "JPEG":
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="JPEG", quality=92)
            data = buf.getvalue()
    except Exception: pass
    return base64.b64encode(data).decode()

def esc(s): return html.escape(str(s or "").strip() or "—")

def build_data(runs):
    out = []
    for run in runs:
        steps_out = []
        for rec in run["steps"]:
            steps_out.append({
                "step_idx":      rec.get("step_idx", 0),
                "action_json":   json.dumps(rec.get("action_json") or {}, ensure_ascii=False, indent=2),
                "summary":       str(rec.get("summary") or ""),
                "action_reason": str(rec.get("action_reason") or ""),
                "action_output": str(rec.get("action_output") or ""),
                "action_raw":    str(rec.get("action_model_raw") or ""),
                "summary_raw":   str(rec.get("summary_model_raw") or ""),
                "img_raw": img_b64(rec.get("before_raw_png_abs", "")),
                "img_bsm": img_b64(rec.get("before_som_png_abs", "")),
                "img_asm": img_b64(rec.get("after_som_png_abs", "")),
            })
            print(f"  encoded step {rec.get('step_idx')}  ", end="\r")
        out.append({
            "label":   run["label"],
            "success": run["tr"].get("is_successful"),
            "steps":   steps_out,
        })
    print()
    return out

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PKL Step Viewer</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f5f6f8;--surface:#ffffff;--panel:#f0f1f4;--border:#dde1e9;
  --accent:#2563eb;--green:#16a34a;--red:#dc2626;--amber:#d97706;
  --text:#1a1f2e;--muted:#6b7280;--muted2:#9ca3af;
}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--text);
  font-family:'Inter',sans-serif;font-size:13px;}

/* ── TOP BAR ── */
#topbar{height:44px;background:var(--surface);border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:10px;padding:0 14px;flex-shrink:0;
  box-shadow:0 1px 3px rgba(0,0,0,.06);}
.logo{font-family:'Inter',sans-serif;font-weight:700;font-size:13px;
  color:var(--accent);letter-spacing:.04em;white-space:nowrap;}
.vdiv{width:1px;height:24px;background:var(--border);flex-shrink:0;}
#run-select{flex:1;max-width:640px;background:var(--surface);color:var(--text);
  border:1px solid var(--border);border-radius:6px;padding:5px 8px;
  font-family:inherit;font-size:12px;outline:none;cursor:pointer;}
#run-select:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(37,99,235,.1);}
.tag{font-size:11px;font-weight:500;padding:2px 8px;border-radius:20px;white-space:nowrap;}
.tag-ok  {background:#dcfce7;color:var(--green);}
.tag-err {background:#fee2e2;color:var(--red);}
.tag-dim {background:var(--panel);color:var(--muted);}
.tag-blue{background:#dbeafe;color:var(--accent);}

/* ── NAV BAR ── */
#navbar{height:40px;background:var(--surface);border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:8px;padding:0 14px;flex-shrink:0;}
button{background:var(--surface);color:var(--text);border:1px solid var(--border);
  border-radius:6px;padding:4px 10px;font-family:inherit;font-size:12px;font-weight:500;
  cursor:pointer;transition:all .12s;}
button:hover{background:var(--panel);border-color:var(--accent);color:var(--accent);}
button:active{transform:scale(.96);}
#step-input{width:52px;background:var(--surface);color:var(--text);
  border:1px solid var(--border);border-radius:6px;padding:4px 6px;
  font-family:'JetBrains Mono',monospace;font-size:12px;text-align:center;outline:none;}
#step-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(37,99,235,.1);}
#step-total{color:var(--muted);font-size:12px;font-family:'JetBrains Mono',monospace;}
.prog-wrap{flex:1;height:4px;background:var(--panel);border-radius:2px;}
.prog-bar{height:100%;background:var(--accent);border-radius:2px;transition:width .2s;}
#raw-toggle{margin-left:auto;}

/* ── LAYOUT ── */
#main{display:flex;flex:1;overflow:hidden;position:relative;}

/* ── LEFT PANEL ── */
#left{width:360px;min-width:180px;max-width:60vw;
  border-right:1px solid var(--border);overflow-y:auto;
  padding:14px;display:flex;flex-direction:column;gap:12px;
  background:var(--surface);flex-shrink:0;}
.section{display:flex;flex-direction:column;gap:4px;}
.sec-label{font-size:10px;font-weight:600;text-transform:uppercase;
  letter-spacing:.1em;color:var(--muted2);}
.sec-val{font-size:12px;font-family:'JetBrains Mono',monospace;
  background:var(--bg);border:1px solid var(--border);
  border-radius:6px;padding:8px 10px;white-space:pre-wrap;word-break:break-all;
  max-height:140px;overflow-y:auto;line-height:1.6;color:var(--text);}
.sec-action{font-size:12px;font-family:'JetBrains Mono',monospace;
  background:#fffbeb;border:1px solid #fcd34d;
  border-radius:6px;padding:8px 10px;white-space:pre-wrap;word-break:break-all;
  line-height:1.6;color:#92400e;}
#raw-section{display:none;flex-direction:column;gap:10px;}
#raw-section .sec-val{max-height:180px;font-size:11px;color:var(--muted);}

/* ── DRAG HANDLE ── */
#drag-handle{
  width:6px;flex-shrink:0;cursor:col-resize;
  background:transparent;position:relative;z-index:10;
  transition:background .15s;
}
#drag-handle:hover,#drag-handle.dragging{background:var(--accent);}
#drag-handle::after{
  content:'';position:absolute;top:50%;left:50%;
  transform:translate(-50%,-50%);
  width:2px;height:32px;border-radius:1px;
  background:var(--border);
}
#drag-handle:hover::after,#drag-handle.dragging::after{background:var(--accent);}

/* ── RIGHT PANEL ── */
#right{flex:1;overflow:auto;padding:14px;display:flex;gap:12px;
  align-items:flex-start;background:var(--bg);}
/* each col takes equal share of right panel width */
.img-col{flex:1;min-width:0;display:flex;flex-direction:column;gap:6px;align-items:stretch;}
.img-cap{font-size:10px;font-weight:600;color:var(--muted);
  text-transform:uppercase;letter-spacing:.08em;text-align:center;}
.img-box{border:1px solid var(--border);border-radius:8px;overflow:hidden;
  background:var(--surface);box-shadow:0 1px 4px rgba(0,0,0,.07);
  display:flex;align-items:flex-start;justify-content:center;}
.img-box img{display:block;width:100%;height:auto;}
.img-miss{height:440px;display:flex;align-items:center;
  justify-content:center;color:var(--muted2);font-size:11px;}

/* ── SCROLLBAR ── */
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:var(--muted2)}
</style>
</head>
<body>
<div style="display:flex;flex-direction:column;height:100vh;">

<div id="topbar">
  <div class="logo">&#9654; Step Viewer</div>
  <div class="vdiv"></div>
  <select id="run-select"></select>
  <span id="status-tag" class="tag tag-dim">—</span>
</div>

<div id="navbar">
  <button id="btn-prev">&#9664; Prev</button>
  <input id="step-input" type="number" min="1" value="1">
  <span id="step-total">/ 1</span>
  <button id="btn-next">Next &#9654;</button>
  <div class="prog-wrap"><div class="prog-bar" id="prog-bar"></div></div>
  <button id="raw-toggle">Raw output</button>
</div>

<div id="main">
  <div id="left">
    <div class="section">
      <div class="sec-label">Action</div>
      <div class="sec-action" id="f-action"></div>
    </div>
    <div class="section">
      <div class="sec-label">Summary</div>
      <div class="sec-val" id="f-summary"></div>
    </div>
    <div class="section">
      <div class="sec-label">Action Reason</div>
      <div class="sec-val" id="f-reason"></div>
    </div>
    <div class="section">
      <div class="sec-label">Action Output</div>
      <div class="sec-val" id="f-output"></div>
    </div>
    <div id="raw-section">
      <div class="section">
        <div class="sec-label">action_model_raw</div>
        <div class="sec-val" id="f-araw"></div>
      </div>
      <div class="section">
        <div class="sec-label">summary_model_raw</div>
        <div class="sec-val" id="f-sraw"></div>
      </div>
    </div>
  </div>

  <div id="drag-handle" title="Drag to resize"></div>

  <div id="right">
    <div class="img-col">
      <div class="img-cap">Before Raw</div>
      <div class="img-box" id="box-raw"></div>
    </div>
    <div class="img-col">
      <div class="img-cap">Before SoM</div>
      <div class="img-box" id="box-bsm"></div>
    </div>
    <div class="img-col">
      <div class="img-cap">After SoM</div>
      <div class="img-box" id="box-asm"></div>
    </div>
  </div>
</div>

</div>
<script>
const RUNS = /*DATA*/;
let curRun = 0, curStep = 1, showRaw = false;
const $ = id => document.getElementById(id);

function imgHtml(b64) {
  if (!b64) return '<div class="img-miss">missing</div>';
  return `<img src="data:image/jpeg;base64,${b64}">`;
}

function render() {
  const run = RUNS[curRun], steps = run.steps, total = steps.length;
  curStep = Math.max(1, Math.min(curStep, total));
  const rec = steps[curStep - 1];
  const pct = total > 1 ? Math.round((curStep-1)/(total-1)*100) : 100;

  $('step-input').value = curStep;
  $('step-total').textContent = `/ ${total}`;
  $('prog-bar').style.width = pct + '%';

  const s = run.success;
  $('status-tag').textContent = s === true ? '✓ success' : s === false ? '✗ failed' : 'unknown';
  $('status-tag').className = 'tag ' + (s === true ? 'tag-ok' : s === false ? 'tag-err' : 'tag-dim');

  $('f-action').textContent  = rec.action_json;
  $('f-summary').textContent = rec.summary   || '—';
  $('f-reason').textContent  = rec.action_reason || '—';
  $('f-output').textContent  = rec.action_output || '—';
  $('f-araw').textContent    = rec.action_raw || '—';
  $('f-sraw').textContent    = rec.summary_raw || '—';

  $('box-raw').innerHTML = imgHtml(rec.img_raw);
  $('box-bsm').innerHTML = imgHtml(rec.img_bsm);
  $('box-asm').innerHTML = imgHtml(rec.img_asm);
}

// ── run select ──
const sel = $('run-select');
sel.innerHTML = RUNS.map((r,i) => `<option value="${i}">${r.label.replace(/&/g,'&amp;').replace(/</g,'&lt;')}</option>`).join('');
sel.addEventListener('change', () => { curRun = +sel.value; curStep = 1; render(); });

// ── nav ──
$('btn-prev').addEventListener('click', () => { curStep--; render(); });
$('btn-next').addEventListener('click', () => { curStep++; render(); });
$('step-input').addEventListener('change', e => { curStep = +e.target.value; render(); });
document.addEventListener('keydown', e => {
  if (['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)) return;
  if (e.key==='ArrowRight'||e.key==='ArrowDown') { curStep++; render(); }
  if (e.key==='ArrowLeft' ||e.key==='ArrowUp')   { curStep--; render(); }
});

// ── raw toggle ──
$('raw-toggle').addEventListener('click', () => {
  showRaw = !showRaw;
  $('raw-section').style.display = showRaw ? 'flex' : 'none';
  $('raw-toggle').className = showRaw ? 'button tag-blue' : 'button';
  $('raw-toggle').style.color = showRaw ? 'var(--accent)' : '';
  $('raw-toggle').style.borderColor = showRaw ? 'var(--accent)' : '';
});

// ── drag to resize ──
(function() {
  const handle = $('drag-handle'), left = $('left');
  let dragging = false, startX = 0, startW = 0;
  handle.addEventListener('mousedown', e => {
    dragging = true; startX = e.clientX; startW = left.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const w = Math.max(180, Math.min(window.innerWidth * 0.65, startW + e.clientX - startX));
    left.style.width = w + 'px';
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
})();

render();
</script>
</body>
</html>
"""
def build_html(runs_data):
    data_json = json.dumps(runs_data, ensure_ascii=False)
    return HTML_TEMPLATE.replace("/*DATA*/", data_json)

def find_pkls(path: Path):
    """Accept a single .pkl file or a directory; return sorted list of .pkl paths."""
    if path.is_file():
        return [path]
    pkls = sorted(path.glob("*.pkl"))
    if not pkls:
        print(f"ERROR: no .pkl files found in {path}"); sys.exit(1)
    return pkls

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="path to a .pkl file OR a folder containing .pkl files")
    ap.add_argument("--out", default="", help="output html path (default: viewer.html next to input)")
    ap.add_argument("--repo-root", default="", help="repo root for resolving relative paths")
    args = ap.parse_args()

    global REPO_ROOT
    if args.repo_root:
        REPO_ROOT = Path(args.repo_root).resolve()

    input_path = Path(args.path).expanduser().resolve()
    pkl_paths  = find_pkls(input_path)

    if args.out:
        out_path = Path(args.out)
    elif input_path.is_dir():
        out_path = input_path / "viewer.html"
    else:
        out_path = input_path.with_suffix(".html")

    print(f"Found {len(pkl_paths)} pkl file(s):")
    for p in pkl_paths:
        print(f"  {p.name}")

    all_runs = []
    for pkl_path in pkl_paths:
        print(f"\nLoading {pkl_path.name} ...")
        runs = load_runs(pkl_path)
        if not runs:
            print(f"  WARNING: no step records, skipping")
            continue
        # prefix each run label with the pkl filename so they're distinguishable
        for r in runs:
            r["label"] = f"[{pkl_path.stem}]  {r['label']}"
        print(f"  {len(runs)} run(s), encoding images...")
        all_runs.extend(runs)

    if not all_runs:
        print("ERROR: no step records found in any pkl"); sys.exit(1)

    runs_data = build_data(all_runs)
    html_str  = build_html(runs_data)

    out_path.write_text(html_str, encoding="utf-8")
    print(f"\nDone → {out_path}  ({out_path.stat().st_size // 1024} KB)")
    print("Open in browser, or: python -m http.server 8888 --directory " + str(out_path.parent))

if __name__ == "__main__":
    main()
