"""Test bench page (/bench on the controller): try every vibe, vibe change, effect, zone mix and
auto mode on the facade, see the measured frame rates (bench/*.json from tools/pb_bench.py) next
to the live one, and leave notes (saved with a settings snapshot, like the vibes page).
"""

import json
import pathlib
import threading

import facade_cues_job
import facade_record_job
from facade_notes import NotesStore
from facade_zones import VIBE_LABELS

ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH_DIR = ROOT / "bench"
VIBES = ["cozy", "party", "chaos", "wave", "jumping", "ethereal", "snake", "twinkle", "heartbeat",
         "aurora", "meteor", "bloom", "kaleido",
         "lotus", "spectrum", "fireflies", "cascade", "mycelium", "butterfly",
         "mosaic", "wings", "vortex"]
EFFECTS = ["flash", "pulse", "color", "charge", "sweep", "shockwave", "rings", "spiral", "wipe", "rain", "comet",
           "halo", "iris", "petalfall", "sunburst", "sporeburst"]
ZONE_MIXES = {   # same mixes as pb_bench.py
    "circle kaleido / mushrooms cozy / leaves aurora": dict(vibe=9, zv0=12, zv1=12, zv2=0, zv3=0),
    "circle bloom / mushrooms meteor / leaves ethereal": dict(vibe=5, zv0=11, zv1=11, zv2=10, zv3=10),
    "every zone different": dict(vibe=1, zv0=12, zv1=11, zv2=0, zv3=10, zv4=9, zv5=5, zv6=7),
}
NO_ZONES = {f"zv{k}": -1 for k in range(7)}


def scenarios():
    out = []
    for v in VIBES:
        out.append({"key": f"vibe:{v}", "group": "Vibes", "title": VIBE_LABELS.get(v, v)})
    for i, v in enumerate(VIBES):
        prev = VIBES[(i + 5) % len(VIBES)]
        out.append({"key": f"transition:{prev}->{v}", "group": "Vibe changes",
                    "title": f"{VIBE_LABELS.get(prev, prev)} → {VIBE_LABELS.get(v, v)}"})
    for host in ("ethereal", "party"):
        for fx in EFFECTS:
            out.append({"key": f"effect:{fx}@{host}", "group": f"Effects on {host}", "title": fx})
    for name in ZONE_MIXES:
        out.append({"key": f"zones:{name}", "group": "Zone mixes", "title": name})
    out.append({"key": "auto:6 s changes for 36 s", "group": "Auto mode", "title": "full auto, a change every 6 s"})
    return out


def bench_runs():
    files = sorted(BENCH_DIR.glob("*.json"))
    runs = []
    for f in files:
        try:
            runs.append({"name": f.stem, "results": json.loads(f.read_text(encoding="utf-8"))})
        except ValueError:
            pass
    return runs


def run_scenario(key, facade):
    """Put the facade into the scenario (through the bridge's link, never saved)."""
    link = facade.link
    kind, _, rest = key.partition(":")
    link.set(autoMode=0, **NO_ZONES)
    if kind == "vibe":
        facade.set_vibe(rest)
    elif kind == "transition":
        a, b = rest.split("->")
        facade.set_vibe(a)
        threading.Timer(3.0, lambda: facade.set_vibe(b)).start()     # let the first vibe settle
    elif kind == "effect":
        fx, host = rest.split("@")
        facade.set_vibe(host)
        threading.Timer(0.3, lambda: facade.effect(fx, {"color": "#ff4fd8", "intensity": 1}, local=True)).start()
    elif kind == "zones":
        mix = ZONE_MIXES.get(rest, {})
        link.set(**{**NO_ZONES, **mix})
    elif kind == "auto":
        link.set(vibe=5, autoSeconds=6, autoLeft=6, autoMode=1)
    return True


def handle_get(path, store: NotesStore, facade):
    if path in ("/bench", "/bench/"):
        return 200, PAGE, "text/html; charset=utf-8"
    if path == "/api/bench":
        return 200, json.dumps({"scenarios": scenarios(), "runs": bench_runs(),
                                "notes": [c for c in store.all() if c["target"].startswith("test:")]}), "application/json"
    rec = facade_record_job.handle_get(path)
    if rec is None:
        rec = facade_cues_job.handle_get(path)
    if rec is not None:
        return 200, json.dumps(rec), "application/json"
    if path == "/api/live":
        return 200, json.dumps({"fps": facade.link.fps, "vmerr": facade.link.vmerr, "vibe": facade.state()["vibe"]}), \
            "application/json"
    return None


def handle_post(path, body, store: NotesStore, facade):
    rec = facade_record_job.handle_post(path, body)
    if rec is None:
        rec = facade_cues_job.handle_post(path, body)
    if rec is not None:
        return 200, rec
    if path == "/api/bench/run":
        return 200, {"ok": run_scenario(str(body.get("key", "")), facade)}
    if path == "/api/bench/reset":
        facade.link.set(autoMode=0, **NO_ZONES)
        facade.set_vibe("ethereal")
        return 200, {"ok": True}
    return None


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Facade Test Bench</title>
<style>
:root{--bg:#0b0d12;--panel:#151922;--line:#262c38;--ink:#e9edf3;--mute:#8a93a3;--hi:#ffd166;--ok:#4dffa6;--warn:#ffb347;--bad:#ff5d73}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{display:flex;align-items:center;gap:14px;padding:12px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:3;flex-wrap:wrap}
header h1{font-size:17px;margin:0;font-weight:650}
header a{color:var(--hi);text-decoration:none;font-size:14px}
.live{margin-left:auto;display:flex;align-items:baseline;gap:8px}
.live b{font-size:28px;font-variant-numeric:tabular-nums}
.live span{color:var(--mute);font-size:13px}
main{max-width:1150px;margin:0 auto;padding:12px 16px 48px;display:grid;gap:16px}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:1.2px;color:var(--mute);margin:0 0 10px;font-weight:600;display:flex;gap:10px;align-items:center}
.legend{font-size:12px;color:var(--mute);text-transform:none;letter-spacing:0;font-weight:400}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.card{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:#11151d;display:grid;gap:6px}
.card.on{border-color:var(--hi)}
.card h3{margin:0;font-size:15px;display:flex;gap:6px;align-items:center;justify-content:space-between}
.nums{display:flex;gap:10px;font-size:13px;color:var(--mute);font-variant-numeric:tabular-nums;flex-wrap:wrap}
.fps{font-weight:650}
.g{color:var(--ok)}.a{color:var(--warn)}.r{color:var(--bad)}
.row{display:flex;gap:6px;flex-wrap:wrap}
button{font:inherit;color:var(--ink);background:#1d2330;border:1px solid var(--line);border-radius:9px;padding:6px 12px;cursor:pointer;min-height:34px}
button.primary{background:var(--hi);color:#1a1400;border-color:var(--hi);font-weight:650}
button:active{transform:scale(.97)}
.badge{font-size:11px;padding:2px 7px;border-radius:99px;background:#262c38;color:var(--mute);font-weight:500}
textarea{width:100%;min-height:54px;font:inherit;color:var(--ink);background:#0d1017;border:1px solid var(--line);border-radius:8px;padding:6px 8px;resize:vertical}
.thread{display:none;gap:6px}.card.open .thread{display:grid}
.note{border-top:1px solid var(--line);padding-top:6px;font-size:13px;white-space:pre-wrap}
.note .meta{color:var(--mute);font-size:11px}
.reply{font-size:13px;color:#cfe3ff;border-left:2px solid var(--hi);padding-left:8px;white-space:pre-wrap}
.note.done .txt{color:var(--mute);text-decoration:line-through}
.summary{color:var(--mute);font-size:13px}
.stars{display:flex;align-items:center;gap:2px;font-size:20px;line-height:1}
.stars button{background:none;border:none;padding:2px 3px;min-height:0;color:#5b6577;cursor:pointer;font-size:20px}
.stars button.on{color:#ffd166}
.stars button.zero{font-size:12px;border:1px solid var(--line);border-radius:6px;padding:2px 6px;margin-right:4px;color:var(--mute)}
.stars button.zero.on{background:#ff5d73;border-color:#ff5d73;color:#fff}
.stars .lbl{font-size:12px;color:var(--mute);margin-left:6px}
.rec{display:grid;gap:10px}
.rec .big{font-size:56px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1}
.rec .state{font-size:18px;font-weight:600}
.rec .bar{height:8px;background:#0d1017;border-radius:99px;overflow:hidden;border:1px solid var(--line)}
.rec .bar i{display:block;height:100%;background:var(--hi);width:0}
.rec label{color:var(--mute);font-size:13px;display:flex;gap:6px;align-items:center}
.rec input[type=number]{width:64px;font:inherit;color:var(--ink);background:#0d1017;border:1px solid var(--line);border-radius:6px;padding:4px}
#recBtn{font-size:17px;padding:10px 18px}
#cueLog{font:12px/1.6 ui-monospace,Consolas,monospace;color:var(--mute);max-height:220px;overflow:auto}
#cueLog b{color:var(--hi);font-weight:600}
#recLog{font:12px/1.5 ui-monospace,Consolas,monospace;color:var(--mute);max-height:120px;overflow:auto;white-space:pre-wrap}
</style></head><body>
<header><h1>Facade Test Bench</h1><a href="/">controller</a><a href="/vibes">vibes &amp; notes</a>
<button id="reset">Reset to ethereal</button>
<div class="live"><span id="now">—</span><b id="fps">—</b><span>fps live</span></div></header>
<main>
<section><h2>How to use</h2><div class="summary">Press <b>Try</b> to put the facade into a setting, watch it (and the live fps
in the top-right), then leave a note: what looks good, what stutters, what to fix or remove. Numbers are the worst whole second /
the average from the bench runs (<code>tools/pb_bench.py</code>): <span id="runs"></span>. Colours: <span class="g">smooth ≥13</span>,
<span class="a">ok 10–13</span>, <span class="r">choppy &lt;10</span>.</div></section>
<section><h2>Record showreel</h2><div class="rec">
<div class="summary">Opening credits (optional logos, the piece's name, Created by Ben Czech, optional tagline), then every vibe and every effect, with the music. Each take skips Spotify to the next song so it plays from the beginning; Spotify is paused after the last take. Press start, then you have 10 seconds to leave the room.</div>
<div class="row"><label>vibe s <input type="number" id="vs" value="10" min="4" max="30"></label><label>effect s <input type="number" id="es" value="10" min="4" max="30"></label>
<label>takes <input type="number" id="tk" value="5" min="1" max="10"></label>
<label><input type="checkbox" id="sp" checked> start a fresh song each take</label></div>
<div class="row"><button class="primary" id="recBtn">Start in 10 s</button><button id="recCancel">Cancel</button></div>
<div class="state" id="recState">idle</div><div class="big" id="recBig"></div>
<div class="bar"><i id="recBar"></i></div><div id="recLog"></div></div></section>
<section><h2>Music cues</h2><div class="rec">
<div class="summary">A program on the PC listens to the music (the same audio the facade gets) and fires effects on its transition points through this dashboard:
<b>breakdown</b> (bass drops away) → calm vibe + slow pulse + iris · <b>build</b> → growing charge on the beat · <b>drop</b> → white flash, shockwave, rings or sunburst, big vibe ·
<b>phrase</b> every 8 bars → sweep/wipe/rings/halo/sunburst · <b>hit</b> standout kick → sporeburst on the mushrooms · <b>song</b> change → wipe + new vibe.</div>
<div class="row"><button class="primary" id="cueStart">Start cues</button><button id="cueStop">Stop</button><span class="state" id="cueState">stopped</span></div>
<div id="cueCounts" class="summary"></div><div id="cueLog"></div></div></section>
<div id="groups"></div>
</main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let data=null, open=new Set(), current=null;
async function api(p,b){const r=await fetch(p,b?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}:{});return r.json()}
const cls=v=>v==null?"":v>=13?"g":v>=10?"a":"r";
function nums(key){
  const runs=data.runs; if(!runs.length) return '<span>not benched yet</span>';
  const first=runs.find(r=>r.results[key]), last=[...runs].reverse().find(r=>r.results[key]);
  if(!last) return '<span>not benched yet</span>';
  const f=first.results[key], l=last.results[key];
  const cell=(r,label)=>`<span>${label} <span class="fps ${cls(r.min)}">${r.min??"–"}</span> / ${r.avg??"–"}</span>`;
  return (first!==last?cell(f,"first")+" → ":"")+cell(l,"now");
}
function card(sc){
  const notes=data.notes.filter(n=>n.target==="test:"+sc.key), openN=notes.filter(n=>n.status==="open").length;
  const el=document.createElement("div"); el.className="card"+(open.has(sc.key)?" open":"")+(current===sc.key?" on":"");
  el.innerHTML=`<h3><span>${esc(sc.title)}</span>${notes.length?`<span class="badge">${openN} open / ${notes.length}</span>`:""}</h3>
  <div class="nums">${nums(sc.key)}</div>
  ${starsHtml("test:"+sc.key)}
  <div class="row"><button class="primary" data-a="try">Try</button><button data-a="notes">${open.has(sc.key)?"Hide notes":"Notes"}</button></div>
  <div class="thread"><textarea placeholder="How does it look? Smooth? Worth keeping?"></textarea><div class="row"><button data-a="save">Save note</button></div>
  ${notes.map(n=>`<div class="note ${n.status}"><div class="meta">${esc(n.at)} · ${n.status}</div><div class="txt">${esc(n.text)}</div>${n.reply?`<div class="reply">${esc(n.reply)}</div>`:""}</div>`).join("")}</div>`;
  bindStars(el);
  el.querySelector('[data-a="try"]').onclick=async()=>{current=sc.key;await api("/api/bench/run",{key:sc.key});render();$("now").textContent=sc.title};
  el.querySelector('[data-a="notes"]').onclick=()=>{open.has(sc.key)?open.delete(sc.key):open.add(sc.key);render()};
  el.querySelector('[data-a="save"]').onclick=async()=>{const t=el.querySelector("textarea").value.trim();if(!t)return;
    await api("/api/notes",{target:"test:"+sc.key,text:t});await load()};
  return el}
function render(){
  const g=$("groups");g.innerHTML="";const groups={};
  data.scenarios.forEach(s=>(groups[s.group]=groups[s.group]||[]).push(s));
  Object.entries(groups).forEach(([name,list])=>{const sec=document.createElement("section");
    sec.innerHTML=`<h2>${esc(name)}</h2><div class="cards"></div>`;list.forEach(s=>sec.querySelector(".cards").appendChild(card(s)));g.appendChild(sec)});
  $("runs").textContent=data.runs.length?`${data.runs.length} runs, first ${data.runs[0].name}, latest ${data.runs[data.runs.length-1].name}`:"no runs yet";
}

let RATINGS={};
async function loadRatings(){try{RATINGS=(await api("/api/ratings")).ratings||{}}catch(e){}}
function starsHtml(target){const r=RATINGS[target],v=r?r.stars:null;let h=`<div class="stars" data-t="${esc(target)}"><button class="zero${v===0?" on":""}" data-s="0" title="0 - bad">0</button>`;
  for(let i=1;i<=5;i++)h+=`<button class="${v!=null&&v>=i?"on":""}" data-s="${i}" title="${i} star${i>1?"s":""}">★</button>`;
  return h+`<span class="lbl">${v==null?"not rated":v+"/5"}</span></div>`}
function bindStars(root){root.querySelectorAll(".stars").forEach(w=>w.querySelectorAll("button").forEach(b=>b.onclick=async(e)=>{e.stopPropagation();
  const t=w.dataset.t,n=+b.dataset.s;await api("/api/rating",{target:t,stars:n});RATINGS[t]={stars:n};w.outerHTML=starsHtml(t);
  bindStars(root)}))}
async function load(){await loadRatings();data=await api("/api/bench");render()}
async function live(){try{const l=await api("/api/live");$("fps").textContent=l.fps?l.fps.toFixed(1):"—";$("fps").className=cls(l.fps);
  if(l.vmerr)$("fps").title="VM error "+l.vmerr}catch(e){$("fps").textContent="offline"}}
$("reset").onclick=async()=>{current=null;await api("/api/bench/reset",{});$("now").textContent="ethereal";render()};
async function rec(){try{const r=await api("/api/record");const st=$("recState"),big=$("recBig"),bar=$("recBar");
  big.textContent="";bar.style.width="0";$("recBtn").disabled=["countdown","recording","encoding","done"].includes(r.phase);
  if(r.phase==="countdown"){st.textContent="starting in";big.textContent=Math.ceil(r.remaining)}
  const tk=(r.takes||1)>1?`take ${r.take||1} of ${r.takes} · `:"";
  const NL=String.fromCharCode(10);
  $("recLog").textContent=(r.files||[]).map(f=>`✓ ${f.file} (${f.mb} MB)`+(f.master?`${NL}   master ${f.master} (${f.master_mb} MB)`:"")).join(NL);
  if(r.phase==="recording"){st.textContent=tk+`recording · ${r.segment||""}`+(r.song?` · ♪ ${r.song}`:"");
    big.textContent=r.total?`${Math.floor(r.elapsed/60)}:${String(Math.floor(r.elapsed%60)).padStart(2,"0")} / ${Math.floor(r.total/60)}:${String(r.total%60).padStart(2,"0")}`:"";
    if(r.total)bar.style.width=Math.min(100,r.elapsed/r.total*100)+"%"}
  else if(r.phase==="encoding"){st.textContent=tk+"adding titles + encoding…";bar.style.width="100%"}
  else if(r.phase==="done"){st.textContent=tk+"saved, next take starting…"}
  else if(r.phase==="all done"){st.textContent=`all done · ${(r.files||[]).length} take(s)`+(r.spotify?` · spotify ${r.spotify}`:"");big.textContent="✓"}
  else if(r.phase==="error"){st.textContent="error: "+r.error}
  else st.textContent=r.phase;
}catch(e){}}
$("recBtn").onclick=async()=>{const r=await api("/api/record/start",{countdown:10,vibe_seconds:+$("vs").value,effect_seconds:+$("es").value,takes:+$("tk").value,spotify:$("sp").checked?"next":"none"});if(!r.ok)alert(r.error);rec()};
$("recCancel").onclick=async()=>{await api("/api/record/cancel",{});rec()};
async function cues(){try{const c=await api("/api/cues");
  $("cueState").textContent=c.running?`running · ${Math.round((Date.now()/1000-c.since)/60)} min`:"stopped";
  $("cueStart").disabled=c.running;
  $("cueCounts").textContent=Object.entries(c.counts).map(([k,v])=>`${k} ${v}`).join(" · ");
  $("cueLog").innerHTML=c.log.map(l=>`${esc(l.at)}  <b>${esc(l.kind)}</b>  ${esc(l.text)}`).join("<br>")||"—";
}catch(e){}}
$("cueStart").onclick=async()=>{await api("/api/cues/start",{});cues()};
$("cueStop").onclick=async()=>{await api("/api/cues/stop",{});cues()};
load();live();rec();cues();setInterval(live,1000);setInterval(rec,500);setInterval(cues,1000);
</script></body></html>"""
