"""Vibes & notes: a second controller page (/vibes) for playing vibes and leaving comments.

Every comment is saved to notes/comments.json with a snapshot of the facade's settings at the
moment it was written, so "bloom feels too slow" can be read later next to exactly what was
showing. Comments target the current settings, a vibe, an effect or a zone.
"""

import json
import pathlib
import threading
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOTES_FILE = ROOT / "notes" / "comments.json"
RATINGS_FILE = ROOT / "notes" / "ratings.json"

VIBE_INFO = {
    "cozy": "Embers drifting upward, coals flickering and flaring; the mushrooms breathe deep red, out of step.",
    "party": "Rainbow rings flowing out from the centre.",
    "chaos": "Flickering random hues; flickers harder with the highs.",
    "wave": "A brightness wave rolling left to right, one per bar when synced to the beat.",
    "jumping": "Three sparks glide to a new spot on every beat and burn white when they land close together.",
    "ethereal": "Drifting colour clouds threaded with bright wisps that burn white at their cores.",
    "snake": "Three coloured snakes slithering along the strip, mirrored left and right.",
    "twinkle": "Dim blue base with white sparkles; more sparkles with the highs.",
    "heartbeat": "A red lub-dub rippling out from the centre on every beat.",
    "aurora": "Green-to-purple curtains drifting upward.",
    "meteor": "A meteor shower over a twinkling night sky: white-hot heads, fire or ice tails.",
    "bloom": "Facade only: light flowing out of the flower, each zone in its own colour.",
    "kaleido": "Facade only: six-fold symmetric colour, like the flower of life.",
    "lotus": "Facade only: the lotus opens with the music's energy - golden heart, amber ring, pink petals filling from "
             "base to tip with a white unfolding edge; it closes again in the quiet parts (breathes slowly without music).",
    "spectrum": "Facade only: a radial spectrum analyser - bass glows at the heart, highs at the petal tips, red through "
                "green to violet; each band auto-gains and is smoothed, so it reads the music without flicker.",
    "fireflies": "Facade only: warm fireflies glowing slowly in a dark forest (violet on the mushrooms). As the music gets "
                 "loud they fall into step and pulse together on the beat; more of them with the highs.",
    "cascade": "Facade only: a sheet of light falls from the crown through the flower to the floor once every bar, "
               "leaving a glowing wake; the mossy mushrooms light up where it lands.",
    "mycelium": "Facade only: bioluminescent pulses run along every strip, mirrored left/right and strongest around the "
                "mushrooms, which glow magenta with the bass.",
    "butterfly": "Facade only: colour clouds mirrored left/right like wings, tinted per zone, with the flower of life as "
                 "the golden body pulsing on the beat (smoothed) and the wings lifting with the bass.",
    "mosaic": "Facade only: stained glass - every cut-out pane breathes its own colour (palette per zone), mirrored "
              "left/right; a group of panes lights up on each beat, eight groups over two bars.",
    "wings": "Facade only: two beams sweep from the crown down around both sides of the flower over a bar and back up "
             "over the next, lighting the petals they pass, with white-hot cores and colours that turn as they descend.",
    "vortex": "Facade only: rings drawn into the heart of the flower; the pull twists into mirrored whirlpool arms as "
              "the bass grows and relaxes back to rings when it drops.",
}
EFFECT_INFO = {
    "flash": "Hit the colour, then fade out (0.6 s).",
    "pulse": "Breathe the colour in and out once (2 s).",
    "color": "Tint toward the colour, fading out over 2.5 s.",
    "charge": "Each hit builds up glow in the colour; it bleeds off slowly.",
    "sweep": "A band of colour travels left to right.",
    "shockwave": "One ring expanding from the flower's centre.",
    "rings": "Three rings chasing out from the centre.",
    "spiral": "Three arms turning around the centre.",
    "wipe": "Fill left to right, hold, release.",
    "rain": "Random twinkles in the colour for 4 s.",
    "comet": "A bright head with a fading tail racing along every strip.",
    "halo": "The flower's ring flares in the colour and the glow closes in over the grid (a shockwave running inward).",
    "iris": "The circle closes and reopens: a dark ring runs from the flower's ring to its centre over about a second.",
    "petalfall": "A shadow passes out from the heart over the petals and fades (a dark shockwave).",
    "sunburst": "Twelve still rays shoot from the heart to the tips, easing in and out over 2 s.",
    "sporeburst": "The mushrooms puff a cloud of spores in the colour, which drifts up into the leaves a moment later.",
}


class NotesStore:
    def __init__(self, path: pathlib.Path = NOTES_FILE):
        self.path = path
        self.lock = threading.Lock()

    def _load(self) -> list:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))["comments"]
        except (FileNotFoundError, ValueError, KeyError):
            return []

    def _save(self, comments: list):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"comments": comments}, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)  # never leave a half-written file behind

    def all(self) -> list:
        with self.lock:
            return self._load()

    def add(self, target: str, text: str, snapshot: dict) -> dict:
        note = {"id": uuid.uuid4().hex[:8], "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "target": target, "text": text.strip(), "status": "open", "snapshot": snapshot, "reply": ""}
        with self.lock:
            comments = self._load()
            comments.insert(0, note)
            self._save(comments)
        return note

    def update(self, note_id: str, **fields) -> bool:
        with self.lock:
            comments = self._load()
            for c in comments:
                if c["id"] == note_id:
                    c.update(fields)
                    self._save(comments)
                    return True
        return False

    def delete(self, note_id: str) -> bool:
        with self.lock:
            comments = self._load()
            kept = [c for c in comments if c["id"] != note_id]
            if len(kept) == len(comments):
                return False
            self._save(kept)
        return True


class RatingsStore:
    """0-5 stars per target (vibe:x, effect:x, test:...), with when and a settings snapshot."""

    def __init__(self, path: pathlib.Path = RATINGS_FILE):
        self.path = path
        self.lock = threading.Lock()

    def all(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))["ratings"]
        except (FileNotFoundError, ValueError, KeyError):
            return {}

    def set(self, target: str, stars: int, snapshot: dict) -> dict:
        with self.lock:
            ratings = self.all()
            ratings[target] = {"stars": stars, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "snapshot": snapshot}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"ratings": ratings}, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        return ratings[target]


RATINGS = RatingsStore()


def handle_get(path: str, store: NotesStore):
    """Returns (status, body, content_type) or None if the path isn't ours."""
    if path in ("/vibes", "/vibes/"):
        return 200, NOTES_PAGE, "text/html; charset=utf-8"
    if path == "/api/notes":
        from facade_zones import VIBE_LABELS
        return 200, json.dumps({"comments": store.all(), "vibes": VIBE_INFO, "effects": EFFECT_INFO,
                                "labels": VIBE_LABELS}), "application/json"
    if path == "/api/ratings":
        return 200, json.dumps({"ratings": {k: {"stars": v["stars"], "at": v["at"]} for k, v in RATINGS.all().items()}}), \
            "application/json"
    return None


def handle_post(path: str, body: dict, store: NotesStore, facade):
    if path == "/api/notes":
        text = str(body.get("text", "")).strip()
        if not text:
            return 400, {"error": "empty comment"}
        snap = {k: v for k, v in facade.state().items() if k not in ("last", "error")}
        return 200, store.add(str(body.get("target", "settings")), text[:2000], snap)
    if path == "/api/notes/status":
        return 200, {"ok": store.update(str(body.get("id")), status="done" if body.get("done") else "open")}
    if path == "/api/rating":
        try:
            stars = int(body.get("stars"))
        except (TypeError, ValueError):
            return 400, {"error": "stars must be 0-5"}
        if not 0 <= stars <= 5 or not body.get("target"):
            return 400, {"error": "stars must be 0-5, with a target"}
        snap = {k: v for k, v in facade.state().items() if k not in ("last", "error")}
        return 200, RATINGS.set(str(body["target"]), stars, snap)
    if path == "/api/notes/delete":
        return 200, {"ok": store.delete(str(body.get("id")))}
    return None


NOTES_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Facade Vibes</title>
<style>
:root{--bg:#0b0d12;--panel:#151922;--line:#262c38;--ink:#e9edf3;--mute:#8a93a3;--hi:#ffd166;--ok:#4dffa6;--bad:#ff5d73}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:3;flex-wrap:wrap}
header h1{font-size:17px;margin:0;font-weight:650}
header a{color:var(--hi);text-decoration:none;font-size:14px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--bad)}.dot.on{background:var(--ok)}
#status{color:var(--mute);font-size:13px;margin-left:auto}
main{max-width:1100px;margin:0 auto;padding:12px 16px 48px;display:grid;gap:16px}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:1.2px;color:var(--mute);margin:0 0 10px;font-weight:600}
button{font:inherit;color:var(--ink);background:#1d2330;border:1px solid var(--line);border-radius:9px;padding:8px 12px;cursor:pointer;min-height:38px}
button:active{transform:scale(.97)}
button.sel,.card.sel{border-color:var(--hi)}
button.sel{color:var(--hi)}
button.primary{background:var(--hi);color:#1a1400;border-color:var(--hi);font-weight:650}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.card{border:1px solid var(--line);border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:8px;background:#11151d}
.card h3{margin:0;font-size:16px;display:flex;align-items:center;gap:8px}
.card p{margin:0;color:var(--mute);font-size:13px;flex:1}
.badge{font-size:11px;padding:2px 7px;border-radius:99px;background:#262c38;color:var(--mute);font-weight:500}
.badge.live{background:#3a3110;color:var(--hi)}
.row{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
textarea{width:100%;min-height:62px;font:inherit;color:var(--ink);background:#0d1017;border:1px solid var(--line);border-radius:8px;padding:8px;resize:vertical}
.now{display:grid;gap:10px}
@media(min-width:760px){.now{grid-template-columns:1.2fr 1fr}}
dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:0;font-size:14px}
dt{color:var(--mute)}dd{margin:0}
.note{border-top:1px solid var(--line);padding:10px 0;display:grid;gap:4px}
.note:first-child{border-top:none}
.note .meta{font-size:12px;color:var(--mute);display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.note .text{white-space:pre-wrap}
.note.done .text{color:var(--mute);text-decoration:line-through}
.note .snap{font-size:12px;color:var(--mute)}
.note .reply{font-size:13px;color:#cfe3ff;border-left:2px solid var(--hi);padding:2px 0 2px 8px;white-space:pre-wrap}
.note .acts{display:flex;gap:6px}
.note .acts button{min-height:30px;padding:4px 10px;font-size:13px}
.thread{display:none}.card.open .thread{display:block}
.thread .note{padding:6px 0}
.empty{color:var(--mute);font-size:13px}
.stars{display:flex;align-items:center;gap:2px;font-size:20px;line-height:1}
.stars button{background:none;border:none;padding:2px 3px;min-height:0;color:#5b6577;cursor:pointer;font-size:20px}
.stars button.on{color:#ffd166}
.stars button.zero{font-size:12px;border:1px solid var(--line);border-radius:6px;padding:2px 6px;margin-right:4px;color:var(--mute)}
.stars button.zero.on{background:#ff5d73;border-color:#ff5d73;color:#fff}
.stars .lbl{font-size:12px;color:var(--mute);margin-left:6px}
</style></head><body>
<header><span class="dot" id="dot"></span><h1>Facade Vibes</h1><a href="/">← controller</a><div id="status">connecting...</div></header>
<main>
<section><h2>Now playing</h2>
<div class="now"><dl id="now"></dl>
<div><textarea id="nowText" placeholder="What do you think of what's showing right now? (saved with a snapshot of these settings)"></textarea>
<div class="row" style="margin-top:6px"><button class="primary" id="nowSave">Comment on current settings</button><span class="empty" id="nowMsg"></span></div></div></div></section>

<section><h2>Play vibes on</h2><div class="chips" id="zones"></div></section>

<section><h2>Vibes</h2><div class="cards" id="vibeCards"></div></section>
<section><h2>Effects · fire white on the zone above</h2><div class="cards" id="fxCards"></div></section>

<section><h2>All notes</h2>
<div class="chips" style="margin-bottom:8px"><button data-f="open" class="sel">Open</button><button data-f="done">Done</button><button data-f="all">All</button></div>
<div id="notes"></div></section>
</main>
<script>
const $=id=>document.getElementById(id);
let st={}, notes=[], info={vibes:{},effects:{}}, zone="all", filter="open", openCards=new Set();
const ZONES=["all","circle","ring","grid","mushrooms","leaves","crown","left","right"];
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function api(p,b){const r=await fetch(p,b?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}:{});return r.json()}
function snapText(s){if(!s)return"";const zv=Object.entries(s.zone_vibes||{}).filter(([,v])=>v).map(([z,v])=>`${z}=${v}`);
  return `vibe ${s.vibe}${zv.length?" ("+zv.join(", ")+")":""} · ${Math.round((s.fader??1)*100)}% · speed ${(s.speed??1).toFixed(2)} · split ${s.split?"on":"off"} · camp ${s.mode}${s.bpm?" · "+Math.round(s.bpm)+" bpm":""}`}
function noteHtml(n,compact){return `<div class="note ${n.status}" data-id="${n.id}">
  <div class="meta"><span class="badge">${esc(n.target)}</span><span>${esc(n.at)}</span></div>
  <div class="text">${esc(n.text)}</div>${n.reply?`<div class="reply">${esc(n.reply)}</div>`:""}${compact?"":`<div class="snap">${esc(snapText(n.snapshot))}</div>`}
  <div class="acts"><button data-act="toggle">${n.status==="done"?"Reopen":"Mark done"}</button><button data-act="del">Delete</button></div></div>`}
function bindNoteActs(root){root.querySelectorAll(".note").forEach(el=>{
  el.querySelector('[data-act="toggle"]').onclick=async()=>{const n=notes.find(x=>x.id===el.dataset.id);await api("/api/notes/status",{id:n.id,done:n.status!=="done"});load()};
  el.querySelector('[data-act="del"]').onclick=async()=>{await api("/api/notes/delete",{id:el.dataset.id});load()}})}
function card(kind,name,desc){const target=`${kind}:${name}`,mine=notes.filter(n=>n.target===target);const open=mine.filter(n=>n.status==="open").length;
  const live=kind==="vibe"&&(st.vibe===name||Object.values(st.zone_vibes||{}).includes(name));
  const el=document.createElement("div");el.className="card"+(live?" sel":"")+(openCards.has(target)?" open":"");
  el.innerHTML=`<h3>${esc((info.labels||{})[name]||name)}${live?'<span class="badge live">on</span>':""}${mine.length?`<span class="badge">${open} open / ${mine.length}</span>`:""}</h3><p>${esc(desc)}</p>
  ${starsHtml(target)}
  <div class="row"><button class="primary" data-a="go">${kind==="vibe"?"Play":"Fire"}</button><button data-a="note">${openCards.has(target)?"Hide notes":"Notes"}</button></div>
  <div class="thread"><textarea placeholder="Comment on ${esc(name)}..."></textarea><div class="row" style="margin:6px 0"><button data-a="save">Save comment</button></div>
  <div class="list">${mine.map(n=>noteHtml(n,true)).join("")||'<div class="empty">No comments yet.</div>'}</div></div>`;
  el.querySelector('[data-a="go"]').onclick=async()=>{if(kind==="vibe")await api("/api/vibe",{vibe:name,zone});else await api("/api/effect",{effect:name,color:"#ffffff",intensity:1,zone});refresh()};
  el.querySelector('[data-a="note"]').onclick=()=>{openCards.has(target)?openCards.delete(target):openCards.add(target);render()};
  el.querySelector('[data-a="save"]').onclick=async()=>{const t=el.querySelector("textarea").value.trim();if(!t)return;await api("/api/notes",{target,text:t});load()};
  bindNoteActs(el);bindStars(el);return el}
function render(){
  const vc=$("vibeCards");vc.innerHTML="";Object.entries(info.vibes).forEach(([n,d])=>vc.appendChild(card("vibe",n,d)));
  const fc=$("fxCards");fc.innerHTML="";Object.entries(info.effects).forEach(([n,d])=>fc.appendChild(card("effect",n,d)));
  const shown=notes.filter(n=>filter==="all"||n.status===filter);
  $("notes").innerHTML=shown.map(n=>noteHtml(n,false)).join("")||'<div class="empty">Nothing here.</div>';bindNoteActs($("notes"));
}
function renderNow(){const own=Object.entries(st.zone_vibes||{}).filter(([,v])=>v);const zv=own.length?own.map(([z,v])=>`${z}: ${v}`).join(" · ")+" · rest follow "+st.vibe:"all follow "+st.vibe;
  $("now").innerHTML=`<dt>Vibe</dt><dd>${esc(st.vibe)}</dd><dt>Zones</dt><dd>${esc(zv)}</dd><dt>Brightness</dt><dd>${Math.round(st.fader*100)}%</dd>
  <dt>Speed</dt><dd>${st.speed.toFixed(2)}×</dd><dt>Audio split</dt><dd>${st.split?"on":"off"}</dd><dt>Camp</dt><dd>${esc(st.mode)}${st.bpm?" · "+Math.round(st.bpm)+" bpm":" · no beat"}</dd>`}
async function refresh(){try{st=await api("/api/state")}catch(e){$("dot").className="dot";$("status").textContent="bridge offline";return}
  $("dot").className="dot"+(st.pixelblaze?" on":"");$("status").textContent=st.pixelblaze?"pixelblaze ok":"pixelblaze DOWN";renderNow();
  document.querySelectorAll("#vibeCards .card").forEach(c=>{const n=c.querySelector("h3").firstChild.textContent;const live=st.vibe===n||Object.values(st.zone_vibes||{}).includes(n);c.classList.toggle("sel",live)})}

let RATINGS={};
async function loadRatings(){try{RATINGS=(await api("/api/ratings")).ratings||{}}catch(e){}}
function starsHtml(target){const r=RATINGS[target],v=r?r.stars:null;let h=`<div class="stars" data-t="${esc(target)}"><button class="zero${v===0?" on":""}" data-s="0" title="0 - bad">0</button>`;
  for(let i=1;i<=5;i++)h+=`<button class="${v!=null&&v>=i?"on":""}" data-s="${i}" title="${i} star${i>1?"s":""}">★</button>`;
  return h+`<span class="lbl">${v==null?"not rated":v+"/5"}</span></div>`}
function bindStars(root){root.querySelectorAll(".stars").forEach(w=>w.querySelectorAll("button").forEach(b=>b.onclick=async(e)=>{e.stopPropagation();
  const t=w.dataset.t,n=+b.dataset.s;await api("/api/rating",{target:t,stars:n});RATINGS[t]={stars:n};w.outerHTML=starsHtml(t);
  bindStars(root)}))}
async function load(){await loadRatings();const r=await api("/api/notes");notes=r.comments;info={vibes:r.vibes,effects:r.effects,labels:r.labels||{}};await refresh();render()}
ZONES.forEach(z=>{const b=document.createElement("button");b.textContent=z;b.dataset.z=z;b.onclick=()=>{zone=z;document.querySelectorAll("#zones button").forEach(x=>x.classList.toggle("sel",x.dataset.z===z))};$("zones").appendChild(b)});
document.querySelector('#zones [data-z="all"]').classList.add("sel");
document.querySelectorAll("[data-f]").forEach(b=>b.onclick=()=>{filter=b.dataset.f;document.querySelectorAll("[data-f]").forEach(x=>x.classList.toggle("sel",x===b));render()});
$("nowSave").onclick=async()=>{const t=$("nowText").value.trim();if(!t)return;await api("/api/notes",{target:"settings",text:t});$("nowText").value="";$("nowMsg").textContent="saved";setTimeout(()=>$("nowMsg").textContent="",2000);load()};
load();setInterval(refresh,2000);
</script></body></html>"""
