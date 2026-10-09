"""Facade controller: a phone-friendly page served by the bridge (default :8121).
Localhost only unless the bridge runs with --lan; there is no authentication.

Pick a zone (or tap it on the live layout), a colour and an intensity, then fire effects or
set vibes. Local control talks straight to the bridge - it never publishes cmd/ on the bus
(only the mapping engine does that), so it is the device's own console, like campbus.console.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import facade_bench_page
import facade_notes
from facade_zones import GROUPS, VIBE_LABELS, ZONES, load_map, load_zones

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Facade Controller</title>
<style>
:root{--bg:#0b0d12;--panel:#151922;--line:#262c38;--ink:#e9edf3;--mute:#8a93a3;--hi:#ffd166;--ok:#4dffa6;--bad:#ff5d73}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.35 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:2}
header h1{font-size:17px;margin:0;font-weight:650;letter-spacing:.2px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--bad)}
.dot.on{background:var(--ok)}
#status{color:var(--mute);font-size:13px;margin-left:auto;text-align:right}
main{max-width:980px;margin:0 auto;padding:12px 16px 40px;display:grid;gap:14px}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:1.2px;color:var(--mute);margin:0 0 10px;font-weight:600}
svg{width:100%;height:auto;display:block;background:#06080b;border-radius:8px;touch-action:manipulation}
.chips{display:flex;flex-wrap:wrap;gap:6px}
button{font:inherit;color:var(--ink);background:#1d2330;border:1px solid var(--line);border-radius:9px;padding:9px 12px;cursor:pointer;min-height:40px}
button:active{transform:scale(.97)}
button.sel{border-color:var(--hi);box-shadow:0 0 0 1px var(--hi) inset;color:var(--hi)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:6px}
.fx{font-weight:600}
.sw{width:40px;height:40px;border-radius:50%;padding:0;border:2px solid #0000}
.sw.sel{border-color:#fff;box-shadow:none}
.row{display:flex;align-items:center;gap:10px;margin:8px 0}
.row label{width:84px;color:var(--mute);font-size:13px}
.row input[type=range]{flex:1;accent-color:var(--hi)}
.row output{width:42px;text-align:right;font-variant-numeric:tabular-nums}
input[type=color]{width:44px;height:40px;border:none;background:none;padding:0}
.hint{color:var(--mute);font-size:12px;margin-top:6px}
#log{font:12px/1.5 ui-monospace,Consolas,monospace;color:var(--mute);max-height:150px;overflow:auto}
.two{display:grid;gap:14px}
@media(min-width:760px){.two{grid-template-columns:1fr 1fr}}
.zv{font-size:12px;color:var(--mute);margin-top:8px}
.meters{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:10px 0 4px}
.meter{display:grid;gap:4px;font-size:12px;color:var(--mute);text-align:center}
.meter .bar{height:64px;background:#0d1017;border:1px solid var(--line);border-radius:6px;position:relative;overflow:hidden}
.meter .fill{position:absolute;left:0;right:0;bottom:0;background:linear-gradient(0deg,#4dffa6,#ffd166 70%,#ff5d73);transition:height .12s linear}
#live{font-size:13px;color:var(--mute)}#live b{color:var(--ok)}
</style></head><body>
<header><span class="dot" id="dot"></span><h1>Facade Controller</h1><a href="/vibes" style="color:var(--hi);text-decoration:none;font-size:14px">vibes &amp; notes →</a><a href="/bench" style="color:var(--hi);text-decoration:none;font-size:14px">test bench →</a><div id="status">connecting...</div></header>
<main>
<section><h2>Layout · tap a zone</h2><svg id="map"></svg>
<div class="chips" id="zones" style="margin-top:10px"></div>
<div class="zv" id="zv"></div></section>
<div class="two">
<section><h2>Colour &amp; strength</h2>
<div class="chips" id="swatches"></div>
<div class="row"><label>Custom</label><input type="color" id="color" value="#ff00aa"></div>
<div class="row"><label>Intensity</label><input type="range" id="intensity" min="0.05" max="1" step="0.05" value="1"><output id="intensityOut">1.00</output></div>
</section>
<section><h2>Facade</h2>
<div class="row"><label>Brightness</label><input type="range" id="fader" min="0" max="1" step="0.02" value="1"><output id="faderOut"></output></div>
<div class="row"><label>Speed</label><input type="range" id="speed" min="0.25" max="3" step="0.05" value="1"><output id="speedOut"></output></div>
<div class="row"><label>Audio split</label><button id="split">on</button><span class="hint">bass→mushrooms · mids→grid · beat→ring · highs→leaves</span></div>
</section></div>
<section><h2>Audio</h2>
<div class="row"><label>Source</label><div class="chips" id="asrc"></div></div>
<div class="row"><label>Sensitivity</label><input type="range" id="gain" min="-2" max="3" step="0.1" value="0"><output id="gainOut">1.0×</output></div>
<div class="meters"><div class="meter"><div class="bar"><div class="fill" id="mBass"></div></div>bass</div>
<div class="meter"><div class="bar"><div class="fill" id="mMid"></div></div>mids</div>
<div class="meter"><div class="bar"><div class="fill" id="mHigh"></div></div>highs</div>
<div class="meter"><div class="bar"><div class="fill" id="mLoud"></div></div>loud</div></div>
<div id="live">—</div>
<div class="hint">auto = the sensor board while it hears music, else the camp bus. Sensitivity sets how loud the input must be to count as music.</div>
</section>
<section><h2>Full auto</h2>
<div class="row"><label>Auto</label><button id="auto">off</button><span class="hint" id="autoInfo">random vibe, zone vibes, speed and effects</span></div>
<div class="row"><label>Every</label><input type="range" id="autoSec" min="5" max="120" step="5" value="30"><output id="autoSecOut">30 s</output></div>
</section>
<section><h2>Effects · fire on the selected zone</h2><div class="grid" id="effects"></div></section>
<section><h2>Vibes · for the selected zone (all = everything)</h2><div class="grid" id="vibes"></div>
<div class="chips" style="margin-top:8px"><button id="follow">Zone follows global</button><button id="followAll">Reset all zones</button></div></section>
<section><h2>Recent</h2><div id="log"></div></section>
</main>
<script>
const ZONE_COLORS=["#ffd700","#00c8ff","#ff3c3c","#ff3cc8","#3cdc3c","#78ffa0","#ffffff","#ff8282"];
const SWATCHES=["#ffffff","#ff0000","#ff4000","#ffaa00","#ffee00","#40ff00","#00ffcc","#00aaff","#3040ff","#8a2be2","#ff00aa","#ff6699"];
let layout=null, zone="all", color="#ffffff", st={};
const $=id=>document.getElementById(id);
async function api(path,body){const r=await fetch(path,body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{});return r.json()}
function chip(label,on,cls){const b=document.createElement("button");b.textContent=label;if(cls)b.className=cls;b.onclick=on;return b}
function zoneMembers(z){return layout.groups[z]||[z]}
function drawMap(){
  const svg=$("map"),W=1000,H=Math.round(1000/layout.aspect);svg.setAttribute("viewBox",`0 0 ${W} ${H}`);
  const sel=new Set(zoneMembers(zone));let s="";
  layout.points.forEach((p,i)=>{const zn=layout.names[layout.zones[i]];const on=zone==="all"||sel.has(zn);
    s+=`<circle cx="${(p[0]*W).toFixed(1)}" cy="${(p[1]*H).toFixed(1)}" r="${on?5:3.2}" fill="${ZONE_COLORS[layout.zones[i]]}" opacity="${on?1:.22}" data-z="${zn}"/>`});
  svg.innerHTML=s;
}
$("map").addEventListener("click",e=>{
  const svg=$("map"),pt=svg.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;const q=pt.matrixTransform(svg.getScreenCTM().inverse());
  const W=1000,H=1000/layout.aspect;let best=null,bd=1e9;
  layout.points.forEach((p,i)=>{const d=(p[0]*W-q.x)**2+(p[1]*H-q.y)**2;if(d<bd){bd=d;best=i}});
  if(best!==null&&bd<45**2)setZone(layout.names[layout.zones[best]]==="mushroom_both"?"mushrooms":layout.names[layout.zones[best]]);
});
function setZone(z){zone=z;[...$("zones").children].forEach(b=>b.classList.toggle("sel",b.dataset.z===z));drawMap();showZv()}
function showZv(){if(!st.zone_vibes)return;const parts=Object.entries(st.zone_vibes).map(([z,v])=>`${z}: ${v||"↳ "+st.vibe}`);$("zv").textContent="vibes — "+parts.join(" · ")}
function setColor(c){color=c;$("color").value=c;[...$("swatches").children].forEach(b=>b.classList.toggle("sel",b.dataset.c===c))}
async function fire(fx){const body={effect:fx,color,intensity:+$("intensity").value,zone};await api("/api/effect",body);refresh()}
async function vibe(v){await api("/api/vibe",{vibe:v,zone});refresh()}
async function refresh(){
  try{st=await api("/api/state")}catch(e){$("dot").className="dot";$("status").textContent="bridge offline";return}
  $("dot").className="dot"+(st.pixelblaze?" on":"");
  $("status").textContent=`${st.pixelblaze?"pixelblaze ok":"pixelblaze DOWN"} · camp ${st.mode} · ${st.bpm?Math.round(st.bpm)+" bpm":"no beat"} · vibe ${st.vibe}`;
  $("split").textContent=st.split?"on":"off";$("split").classList.toggle("sel",st.split);
  if(document.activeElement!==$("fader")){$("fader").value=st.fader;$("faderOut").textContent=Math.round(st.fader*100)+"%"}
  if(document.activeElement!==$("speed")){$("speed").value=st.speed;$("speedOut").textContent=st.speed.toFixed(2)+"×"}
  [...$("vibes").children].forEach(b=>b.classList.toggle("sel",b.dataset.v===st.vibe));
  $("log").innerHTML=st.last.map(l=>`${l.at}  ${l.effect.padEnd(9)} ${l.zone} ${l.color} ×${l.intensity}  <i>${l.from}</i>`).join("<br>")||"—";
  showZv();
  [...$("asrc").children].forEach(b=>b.classList.toggle("sel",b.dataset.s===st.audio_src));
  if(st.auto){$("auto").textContent=st.auto.on?"on":"off";$("auto").classList.toggle("sel",st.auto.on);
    $("autoInfo").textContent=st.auto.on?`next change in ${st.auto.left} s · now ${st.vibe}`:"random vibe, zone vibes, speed and effects";
    if(document.activeElement!==$("autoSec")){$("autoSec").value=st.auto.seconds;$("autoSecOut").textContent=st.auto.seconds+" s"}}
  if(document.activeElement!==$("gain")){$("gain").value=Math.log2(st.sensor_gain||1);$("gainOut").textContent=(st.sensor_gain||1).toFixed(1)+"×"}
}
async function meters(){try{const a=await api("/api/audio");
  [["mBass","aBass"],["mMid","aMid"],["mHigh","aHigh"],["mLoud","aLoud"]].forEach(([id,k])=>$(id).style.height=Math.round((a[k]||0)*100)+"%");
  $("live").innerHTML=a.aLive?`<b>● sensor hears music</b>${a.aBpm?" · ~"+Math.round(a.aBpm)+" bpm":""}`:"sensor: quiet"+(st.bpm?` · bus ${Math.round(st.bpm)} bpm`:"");
}catch(e){}}
(async()=>{
  layout=await api("/api/layout");
  ["all","circle","ring","grid","mushrooms","mushroom_left","mushroom_right","leaves","leaves_left","leaves_right","crown","left","right"]
    .forEach(z=>{const b=chip(z.replace("_"," "),()=>setZone(z));b.dataset.z=z;$("zones").appendChild(b)});
  SWATCHES.forEach(c=>{const b=chip("",()=>setColor(c),"sw");b.style.background=c;b.dataset.c=c;b.title=c;$("swatches").appendChild(b)});
  $("color").oninput=e=>setColor(e.target.value);
  $("intensity").oninput=e=>$("intensityOut").textContent=(+e.target.value).toFixed(2);
  $("fader").oninput=e=>{$("faderOut").textContent=Math.round(e.target.value*100)+"%";api("/api/set",{fader:+e.target.value})};
  $("speed").oninput=e=>{$("speedOut").textContent=(+e.target.value).toFixed(2)+"×";api("/api/set",{speed:+e.target.value})};
  $("split").onclick=async()=>{await api("/api/set",{split:!st.split});refresh()};
  layout.effects.forEach(fx=>$("effects").appendChild(chip(fx,()=>fire(fx),"fx")));
  layout.vibes.forEach(v=>{const b=chip((layout.labels||{})[v]||v,()=>vibe(v));b.dataset.v=v;$("vibes").appendChild(b)});
  $("follow").onclick=()=>vibe("follow");
  $("followAll").onclick=async()=>{await api("/api/followall",{});refresh()};
  ["auto","sensor","bus"].forEach(s=>{const b=chip(s,async()=>{await api("/api/set",{audio_src:s});refresh()});b.dataset.s=s;$("asrc").appendChild(b)});
  $("gain").oninput=e=>{const g=Math.pow(2,+e.target.value);$("gainOut").textContent=g.toFixed(1)+"×";api("/api/set",{sensor_gain:g})};
  $("auto").onclick=async()=>{await api("/api/set",{auto:!(st.auto&&st.auto.on)});refresh()};
  $("autoSec").oninput=e=>{$("autoSecOut").textContent=e.target.value+" s"};
  $("autoSec").onchange=e=>api("/api/set",{auto_seconds:+e.target.value});
  setZone("all");setColor("#ffffff");refresh();setInterval(refresh,1500);setInterval(meters,250);
})();
</script></body></html>"""


def serve_controller(facade, port: int = 8121, host: str = "127.0.0.1"):
    """Serve the controller page. Binds to localhost by default; the bridge's --lan flag passes
    host="0.0.0.0". There is no authentication, so only expose it on a trusted network."""
    from facade_bridge import EFFECTS, MAP_RUN, VIBES   # avoid a circular import at load time

    pts = load_map(MAP_RUN)
    notes = facade_notes.NotesStore()
    lo, hi = pts.min(0), pts.max(0)
    layout = {
        "points": ((pts - lo) / (hi - lo)).round(4).tolist(),
        "aspect": float((hi - lo)[0] / (hi - lo)[1]),
        "zones": load_zones(MAP_RUN),
        "names": ZONES,
        "groups": GROUPS,
        "effects": EFFECTS,
        "vibes": VIBES,
        "labels": VIBE_LABELS,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            mine = facade_notes.handle_get(self.path, notes) or facade_bench_page.handle_get(self.path, notes, facade)
            if mine:
                return self._send(mine[0], mine[1], mine[2])
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE, "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._send(200, json.dumps(facade.state()))
            elif self.path == "/api/audio":
                facade.link.want_meters()
                self._send(200, json.dumps(facade.link.audio))
            elif self.path == "/api/layout":
                self._send(200, json.dumps(layout))
            else:
                self._send(404, '{"error":"not found"}')

        def do_POST(self):
            # JSON only: a cross-site HTML form can't send application/json without a CORS preflight,
            # which this server never answers, so this blocks simple-request CSRF from other pages.
            ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return self._send(415, '{"error":"Content-Type must be application/json"}')
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except ValueError:
                return self._send(400, '{"error":"bad json"}')
            mine = facade_notes.handle_post(self.path, body, notes, facade) or                 facade_bench_page.handle_post(self.path, body, notes, facade)
            if mine:
                return self._send(mine[0], json.dumps(mine[1], ensure_ascii=False))
            ok = True
            if self.path == "/api/effect":
                ok = facade.effect(body.pop("effect", ""), body, local=True)
            elif self.path == "/api/vibe":
                ok = facade.set_vibe(body.get("vibe"), body.get("zone"))
            elif self.path == "/api/followall":
                facade.follow_all()
            elif self.path == "/api/set":
                if "fader" in body:
                    facade.set_fader(float(body["fader"]))
                if "speed" in body:
                    facade.set_speed(float(body["speed"]))
                if "split" in body:
                    facade.set_split(body["split"])
                if "auto" in body or "auto_seconds" in body:
                    facade.set_auto(body.get("auto"), body.get("auto_seconds"))
                if "audio_src" in body or "sensor_gain" in body:
                    facade.set_audio(body.get("audio_src"), body.get("sensor_gain"))
            else:
                return self._send(404, '{"error":"not found"}')
            self._send(200, json.dumps({"ok": bool(ok)}))

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="facade-controller", daemon=True).start()
    return httpd
