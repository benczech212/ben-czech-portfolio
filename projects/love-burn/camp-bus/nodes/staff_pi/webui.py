"""A tiny live view of the staff's sensors, served by the staff itself.

Point a phone at http://<pi>:8081/ while carrying the staff around and watch the
casting state machine, the attitude, the GPS and the motion gate in real time. The
whole point is tuning: you can see what "still" actually reads on this hardware, and
set the thresholds until arming and sweeping feel right, without an ssh session or a
reboot.

The casting section is built around one idea: **every attempt is shown, especially
the failures.** A gesture detector that silently does nothing when it dislikes what
you did is untunable - you cannot tell "it never armed" from "it armed and decided
that was a shake", and those have opposite fixes. So every capture is logged with the
reason it was rejected and the trace that produced it.

LAYOUT: it is an instrument panel, not a page you scroll.

  * One hand is holding the staff, so the cast loop - state, turn rate, steadiness -
    is pinned to the top of a phone screen and never scrolls away. Everything else is
    a collapsible panel, and the three groups sit behind tabs beneath it.
  * On anything wider it becomes two or three columns at full viewport height with
    nothing to scroll, capped so readings do not end up a hand-span apart on a big
    monitor.
  * State is signalled by brightness rather than by adding colours, the way an
    annunciator panel does: the face fills with light as you hold still and goes
    solid when it is armed. Amber is clockwise, blue is counter-clockwise, and red
    means only one thing - rejected. No other hues, so the ones that are there mean
    something.

The orientation view draws the staff in space from the BNO055's fused quaternion -
which costs nothing, because it already arrives inside the same 32-byte block read as
the gyro and the gravity vector. Quaternion rather than the Euler angles sitting next
to it: no gimbal lock, no wrap at 360, and it is what a renderer wants anyway. Since
nobody knows which way the chip is glued inside the staff, **Zero this pose** takes
the current attitude as upright, so you hold the staff the way you hold it and the
model lines up.

Design notes, both of which matter on this hardware:

  * It shares the running node's sensor objects. A second process opening its own
    BNO055 would fight the first for a bus that already mishandles this chip's
    clock stretching - two readers is how you turn a marginal bus into a broken one.
  * The page is entirely self-contained: no CDN, no fonts, no frameworks, and the 3D
    is a hundred lines of canvas rather than a megabyte of library. At camp the Pi has
    no internet, and a phone that loads a blank page is worse than no page at all.

The Sound panel (2026-10-04) plays the .wav files under --sounds through the WM8960
HAT. The page only asks; sound.py does the playing, in its own aplay process, so a
button press costs the sensor loop nothing. The file list is fetched once, not
streamed - the 10 Hz stream carries only what is playing and the volume.

Security: there is no login, so exposure is controlled by where it listens.
start_web() binds 127.0.0.1 by default; staff_node.py passes host="0.0.0.0" only with
--lan, which is how the Pi serves a phone on the closed camp Wi-Fi. Reads (the page,
/state, /stream, /sound/list) are GET; everything that changes state (/config,
/sound/play, /sound/stop, /sound/volume) is POST-only and must carry the
X-Campbus header, which a cross-site form cannot send without a CORS preflight this
server never answers. Do not expose it on a network you do not control.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

# Every knob the page may turn. staff_node.CONFIG_KEYS decides what each one means;
# this list only decides what is allowed through the door.
CONFIG_FLOATS = ("accel", "gyro", "still", "arm_seconds", "arm_accel", "arm_gyro",
                 "arm_timeout", "trigger_dps", "min_deg", "dir_purity", "axis_purity",
                 "sleep_after", "accel_filter", "gyro_filter")
# Not everything is a number. Each of these is a fixed vocabulary, checked here so a
# typo in a URL cannot set a sensor filter to nonsense.
CONFIG_WORDS = {"filter_mode": ("off", "mean", "median")}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Staff sensors</title>
<style>
  :root {
    --bg:#070a0c; --panel:#0e1519; --line:#1c272d; --sunk:#050809;
    --ink:#e8efec; --dim:#6d8189; --faint:#41535a;
    --cw:#ffa23d;          /* clockwise, and the colour of anything live */
    --ccw:#5fb8ff;         /* counter-clockwise */
    --alarm:#ff5c4d;       /* one meaning only: rejected, or broken */
    --mono:ui-monospace,"SF Mono","Cascadia Mono",Menlo,Consolas,monospace;
  }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  /* an author `canvas { display:block }` outranks the UA's [hidden] rule, and the
     trace canvas has to actually disappear before the first attempt */
  [hidden] { display:none !important; }
  html, body { height:100%; }
  body {
    margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  .num { font-family:var(--mono); font-variant-numeric:tabular-nums; }

  /* ---- status rail ---- */
  #rail {
    height:34px; display:flex; align-items:center; gap:14px;
    padding:0 14px; border-bottom:1px solid var(--line); background:var(--panel);
    font-size:12.5px; color:var(--dim); position:sticky; top:0; z-index:5;
  }
  #rail .id { color:var(--ink); font-weight:600; }
  #rail .sp { margin-left:auto; }
  .dot { width:7px; height:7px; border-radius:50%; background:var(--faint);
         display:inline-block; vertical-align:1px; margin-right:6px; }
  .dot.on { background:var(--cw); box-shadow:0 0 6px var(--cw); }
  .dot.off { background:var(--alarm); }

  /* ---- panel grid ---- */
  #wrap { display:block; }
  .col { min-width:0; }
  .side { display:contents; }      /* a wrapper only where the layout needs one */
  .pane { border-bottom:1px solid var(--line); padding:13px 14px; }
  .pane:last-child { border-bottom:0; }

  /* ---- collapsing ---- */
  details.pane > summary {
    list-style:none; cursor:pointer; display:flex; align-items:center; gap:8px;
    font-size:12.5px; font-weight:600; color:var(--dim); user-select:none;
  }
  details.pane > summary::-webkit-details-marker { display:none; }
  details.pane > summary::before {
    content:''; flex:none; width:0; height:0; border-left:4.5px solid var(--faint);
    border-top:3.5px solid transparent; border-bottom:3.5px solid transparent;
    transition:transform .13s ease;
  }
  details.pane[open] > summary::before { transform:rotate(90deg); }
  details.pane[open] > summary { margin-bottom:11px; }
  details.pane > summary:focus-visible { outline:2px solid var(--cw); outline-offset:3px; }
  summary em { font-style:normal; font-weight:400; color:var(--faint); font-size:11.5px;
               margin-left:auto; text-align:right; }
  summary:hover::before { border-left-color:var(--dim); }

  /* ---- the state face: the one bold thing ---- */
  #face {
    position:relative; overflow:hidden; border:1px solid var(--line); border-radius:4px;
    background:var(--sunk); padding:20px 16px 17px; text-align:center;
    transition:background .14s linear, border-color .14s linear;
  }
  #ffill { position:absolute; inset:0 auto 0 0; width:0%; background:var(--cw);
           opacity:.13; transition:width .1s linear; }
  #fstate, #fsub { position:relative; }
  #fstate { font-size:33px; font-weight:750; letter-spacing:-.01em; line-height:1;
            color:var(--faint); transition:color .14s linear; }
  #fsub { margin-top:7px; font-size:13px; color:var(--faint); min-height:19px; }
  #face.st-idle    #fstate { color:var(--dim); }
  #face.st-arming  { border-color:#3a3020; } #face.st-arming #fstate { color:var(--cw); }
  #face.st-arming  #fsub { color:var(--dim); }
  #face.st-armed   { background:var(--cw); border-color:var(--cw); }
  #face.st-armed   #fstate, #face.st-armed #fsub { color:#120b02; }
  #face.st-armed   #fsub { opacity:.72; }
  #face.st-capture, #face.st-cast { background:var(--ink); border-color:var(--ink); }
  #face.st-capture #fstate, #face.st-capture #fsub,
  #face.st-cast #fstate, #face.st-cast #fsub { color:#06090b; }
  #face.st-reject  { border-color:#4a211d; } #face.st-reject #fstate { color:var(--alarm); }
  #face.st-reject  #fsub { color:#c98a83; }

  /* ---- turn-rate meter ---- */
  .meter { margin-top:13px; }
  .mhead { display:flex; justify-content:space-between; align-items:baseline;
           font-size:12px; color:var(--faint); margin-bottom:6px; }
  #yval { font-size:19px; font-weight:650; color:var(--ink); }
  #yval.cw { color:var(--cw); } #yval.ccw { color:var(--ccw); }
  .gauge { position:relative; height:22px; background:var(--sunk);
           border:1px solid var(--line); border-radius:3px 3px 0 0; overflow:hidden; }
  #ybar { position:absolute; top:0; bottom:0; left:50%; width:0%; background:var(--cw); }
  #ybar.ccw { background:var(--ccw); }
  .zero { position:absolute; left:50%; top:0; bottom:0; width:1px; background:#33444c; }
  .notch { position:absolute; top:0; bottom:0; width:1px; background:var(--faint); }

  canvas { display:block; }
  #ychart { width:100%; height:88px; background:var(--sunk); border:1px solid var(--line);
            border-top:0; border-radius:0 0 3px 3px; }
  #chart, #tchart, #scene { width:100%; background:var(--sunk);
            border:1px solid var(--line); border-radius:3px; }
  #chart { height:84px; margin-top:10px; } #tchart { height:62px; margin-top:8px; }
  #scene { height:230px; }
  #dial { width:118px; height:118px; margin:0 auto 8px; }

  /* ---- readouts ---- */
  .row { display:flex; justify-content:space-between; align-items:baseline; gap:12px;
         font-size:13px; padding:3px 0; }
  .row > span:first-child { color:var(--dim); }
  .row b { font-weight:600; }
  .pass { color:var(--ink); } .fail { color:var(--alarm); } .quiet { color:var(--dim); }

  /* ---- bars ---- */
  .bar { margin:10px 0 0; }
  .bar .lab { display:flex; justify-content:space-between; font-size:12.5px;
              color:var(--dim); margin-bottom:4px; }
  .bar .lab b { color:var(--ink); font-weight:600; }
  .track { position:relative; height:9px; background:var(--sunk); border-radius:2px;
           border:1px solid var(--line); overflow:hidden; }
  .fill { position:absolute; inset:0 auto 0 0; background:var(--ccw); width:0%;
          transition:width .1s linear; }
  .fill.over { background:var(--cw); }
  .thresh { position:absolute; top:-1px; bottom:-1px; width:1px; background:var(--faint); }

  /* ---- attempt log ---- */
  .att { border-top:1px solid var(--line); padding:8px 0; }
  .att:first-child { border-top:0; padding-top:0; }
  .att .top { display:flex; justify-content:space-between; align-items:baseline; gap:10px;
              font-size:13.5px; }
  .att .dir { font-weight:650; }
  .att .dir.cw { color:var(--cw); } .att .dir.ccw { color:var(--ccw); }
  .att .dir.no { color:var(--alarm); font-weight:600; }
  .att .why { color:var(--dim); flex:1; }
  .att .when { color:var(--faint); font-size:12px; }
  .att .stats, .stats { margin-top:3px; font-size:11.5px; color:var(--faint);
                        font-family:var(--mono); line-height:1.6; }
  .empty { color:var(--faint); font-size:13px; }
  #payload { margin-top:8px; padding:8px 9px; background:var(--sunk);
             border:1px solid var(--line); border-radius:3px;
             font-family:var(--mono); font-size:11px; line-height:1.55;
             color:var(--dim); word-break:break-all; }

  /* ---- knobs: a slider and a box you can type in ---- */
  .knob { margin:0 0 12px; }
  .knob .lab { display:flex; justify-content:space-between; align-items:center;
               gap:10px; font-size:13px; color:var(--dim); margin-bottom:2px; }
  .knob .lab label { cursor:default; }
  .val { width:66px; text-align:right; background:transparent;
         border:1px solid transparent; border-radius:3px; padding:2px 4px;
         color:var(--ink); font:inherit; font-family:var(--mono); font-weight:600;
         font-variant-numeric:tabular-nums; -moz-appearance:textfield; }
  .val::-webkit-outer-spin-button, .val::-webkit-inner-spin-button {
         -webkit-appearance:none; margin:0; }
  .val:hover { border-color:var(--line); }
  .val:focus { border-color:var(--cw); outline:none; background:var(--sunk); }
  .val.bad { border-color:var(--alarm); }
  .unit { color:var(--faint); font-size:12px; margin-left:4px; }
  input[type=range] { width:100%; height:18px; margin:0; background:transparent;
                      -webkit-appearance:none; appearance:none; }
  input[type=range]::-webkit-slider-runnable-track { height:3px; background:var(--line); }
  input[type=range]::-moz-range-track { height:3px; background:var(--line); }
  input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:16px; height:16px;
    margin-top:-6.5px; border-radius:50%; background:var(--ink); border:0; }
  input[type=range]::-moz-range-thumb { width:16px; height:16px; border-radius:50%;
    background:var(--ink); border:0; }
  input[type=range]:focus-visible { outline:2px solid var(--cw); outline-offset:3px; }
  .note { color:var(--faint); font-size:11.5px; line-height:1.5; margin:2px 0 0; }

  .seg { display:flex; border:1px solid var(--line); border-radius:3px;
         overflow:hidden; margin:2px 0 4px; }
  .seg button { flex:1; appearance:none; background:transparent; border:0;
    border-right:1px solid var(--line); color:var(--dim); font:inherit; font-size:12.5px;
    padding:6px 0; cursor:pointer; }
  .seg button:last-child { border-right:0; }
  .seg button[aria-pressed=true] { background:var(--cw); color:#120b02; font-weight:600; }
  .seg button:focus-visible { outline:2px solid var(--cw); outline-offset:-3px; }
  .raw { color:var(--faint); font-weight:400; }

  button.act { appearance:none; background:var(--sunk); border:1px solid var(--line);
    color:var(--ink); font:inherit; font-size:12.5px; padding:6px 11px; border-radius:3px;
    cursor:pointer; }
  button.act:hover { border-color:var(--dim); }
  button.act:focus-visible { outline:2px solid var(--cw); outline-offset:2px; }
  .acts { display:flex; gap:8px; margin-top:9px; }

  /* ---- sound ---- */
  #sndnow { display:flex; align-items:center; gap:10px; padding:8px 10px; margin-bottom:10px;
            background:var(--sunk); border:1px solid var(--line); border-radius:3px; }
  #sndnow.on { border-color:var(--cw); }
  #sndname { flex:1; min-width:0; font-size:13px; color:var(--faint);
             overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  #sndnow.on #sndname { color:var(--cw); font-weight:600; }
  .sgrp { font-size:11.5px; color:var(--faint); margin:10px 0 5px; font-family:var(--mono); }
  .sgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(132px,1fr)); gap:6px; }
  .sgrid button { text-align:left; min-height:38px; overflow:hidden; text-overflow:ellipsis;
                  white-space:nowrap; }
  .sgrid button.on { background:var(--cw); border-color:var(--cw); color:#120b02;
                     font-weight:600; }
  button.act:disabled { opacity:.4; cursor:default; }
  /* the knob's pixel, mirrored: hue from the knob, brightness from what it is showing */
  #kswatch { height:30px; border:1px solid var(--line); border-radius:3px; margin-bottom:8px;
             background:var(--sunk); position:relative; overflow:hidden; }
  #kfill { position:absolute; inset:0; opacity:0; transition:opacity .08s linear; }

  /* ---- tabs: phones only ---- */
  #tabs { position:sticky; bottom:0; z-index:5; display:flex; background:var(--panel);
          border-top:1px solid var(--line); }
  #tabs button { flex:1; appearance:none; background:none; border:0;
                 border-top:2px solid transparent; color:var(--dim); font:inherit;
                 font-size:13.5px; padding:11px 4px 13px; cursor:pointer; }
  #tabs button[aria-selected=true] { color:var(--ink); border-top-color:var(--cw); }
  #tabs button:focus-visible { outline:2px solid var(--cw); outline-offset:-4px; }

  @media (max-width:719px) {
    .col[data-col=log], .col[data-col=tune], .col[data-col=sys],
    .col[data-col=snd] { display:none; }
    body[data-tab=log] .col[data-col=log],
    body[data-tab=tune] .col[data-col=tune],
    body[data-tab=sys] .col[data-col=sys],
    body[data-tab=snd] .col[data-col=snd] { display:block; }
  }

  /* ---- bench: everything visible at once, the page itself never scrolls ---- */
  @media (min-width:720px) {
    body { height:100dvh; overflow:hidden; display:flex; flex-direction:column; }
    #tabs { display:none; }
    /* An instrument has a bezel. Past this width the columns stop growing and the
       panel centres, rather than stretching readings a metre apart on a big monitor. */
    #rail, #wrap { width:100%; max-width:1400px; margin-inline:auto; }
    #wrap { flex:1; min-height:0; display:grid; align-self:center;
            border-left:1px solid var(--line); border-right:1px solid var(--line);
            grid-template-columns:minmax(330px,1.05fr) minmax(300px,1fr);
            grid-template-rows:minmax(150px,44%) 1fr; }
    .col, .side { overflow:auto; min-height:0;
                  scrollbar-width:thin; scrollbar-color:var(--line) transparent; }
    .col[data-col=cast] { grid-column:1; grid-row:1 / -1; border-right:1px solid var(--line); }
    .col[data-col=log]  { grid-column:2; grid-row:1; border-bottom:1px solid var(--line); }
    .side { grid-column:2; grid-row:2; display:block; }
  }
  @media (min-width:1180px) {
    #wrap { grid-template-columns:minmax(345px,1fr) minmax(300px,.9fr) minmax(290px,.82fr);
            grid-template-rows:1fr; }
    .col[data-col=log] { grid-row:1; border-bottom:0; border-right:1px solid var(--line); }
    .side { grid-column:3; grid-row:1; }
  }
  @media (prefers-reduced-motion:reduce) { * { transition:none !important; } }
</style></head><body data-tab="log">

<div id="rail">
  <span class="id" id="nodeid">staff</span>
  <span><span class="dot" id="linkdot"></span><span id="conn">connecting</span></span>
  <span class="sp"></span>
  <span id="broker">broker offline</span>
  <span class="num" id="hz">&ndash;</span>
</div>

<div id="wrap">

  <!-- ============ the cast loop: always on screen ============ -->
  <div class="col" data-col="cast">
    <div class="pane">
      <div id="face" class="st-idle">
        <div id="ffill"></div>
        <div id="fstate">&mdash;</div>
        <div id="fsub">waiting for the staff</div>
      </div>

      <div class="meter">
        <div class="mhead">
          <span>counter-clockwise</span>
          <span class="num" id="yval">0&deg;/s</span>
          <span>clockwise</span>
        </div>
        <div class="gauge">
          <div class="zero"></div><div id="ybar"></div>
          <div class="notch" id="ytn"></div><div class="notch" id="ytp"></div>
        </div>
        <canvas id="ychart"></canvas>
      </div>

      <div class="row" style="margin-top:11px">
        <span>Steady enough to arm</span>
        <b class="num"><span id="stacc">&ndash;</span> <span class="quiet">&middot;</span>
           <span id="stgyr">&ndash;</span></b>
      </div>
      <div class="row"><span>Cast, rejected</span><b class="num" id="tally">&ndash;</b></div>
    </div>

    <details class="pane" id="p-last" open>
      <summary>Last attempt</summary>
      <div id="lastverdict" class="empty">Nothing yet. Hold it still, then sweep.</div>
      <canvas id="tchart" hidden></canvas>
      <div id="payload" hidden></div>
    </details>

    <details class="pane" id="p-orient" open>
      <summary>Orientation <em id="orientmode">absolute</em></summary>
      <canvas id="scene"></canvas>
      <div class="acts">
        <button class="act" id="zero">Zero this pose</button>
        <button class="act" id="unzero">Back to absolute</button>
      </div>
      <p class="note">Hold the staff the way you carry it and zero the view, so the model
        stands up the way the staff does. Nobody knows how the chip is glued in.</p>
      <div class="row" style="margin-top:6px"><span>Heading</span><b class="num" id="o_head">&ndash;</b></div>
      <div class="row"><span>Roll</span><b class="num" id="o_roll">&ndash;</b></div>
      <div class="row"><span>Pitch</span><b class="num" id="o_pitch">&ndash;</b></div>
    </details>

    <details class="pane" id="p-raw" open>
      <summary>Raw motion <em id="state">&mdash;</em></summary>
      <div class="bar">
        <div class="lab"><span>Linear acceleration</span>
          <b class="num"><span id="av">0.00</span> m/s&sup2;
            <span class="raw" id="avr"></span></b></div>
        <div class="track"><div class="fill" id="af"></div><div class="thresh" id="at"></div></div>
      </div>
      <div class="bar">
        <div class="lab"><span>Rotation</span>
          <b class="num"><span id="gv">0.0</span> &deg;/s
            <span class="raw" id="gvr"></span></b></div>
        <div class="track"><div class="fill" id="gf"></div><div class="thresh" id="gt"></div></div>
      </div>
      <canvas id="chart"></canvas>
    </details>
  </div>

  <!-- ============ attempts ============ -->
  <div class="col" data-col="log">
    <details class="pane" id="p-log" open>
      <summary>Attempts <em>newest first</em></summary>
      <div id="log" class="empty">Nothing yet. Every sweep shows up here, cast or not.</div>
    </details>
  </div>

  <div class="side">
  <!-- ============ sound ============ -->
  <div class="col" data-col="snd">
    <details class="pane" id="p-knob" open>
      <summary>Spell colour <em id="ksum">&ndash;</em></summary>
      <div id="kswatch"><div id="kfill"></div></div>
      <div class="row"><span>Hue</span><b class="num" id="khue">&ndash;</b></div>
      <div class="row"><span>Pixel brightness</span><b class="num" id="klevel">&ndash;</b></div>
      <div class="row"><span>Button</span><b class="num" id="kbtn">&ndash;</b></div>
      <div class="row"><span>Clicks, presses, bus errors</span><b class="num" id="kstats">&ndash;</b></div>
      <p class="note" id="knote">Turn the knob on the staff to choose. Every cast carries this
        colour; the button previews it without casting.</p>
    </details>
    <details class="pane" id="p-sound" open>
      <summary>Sound <em id="sndsum">&ndash;</em></summary>
      <div id="sndnow">
        <span id="sndname">nothing playing</span>
        <span class="num quiet" id="sndel"></span>
        <button class="act" id="sndstop" disabled>Stop</button>
      </div>
      <div class="knob">
        <div class="lab"><label for="r_vol">Volume <span class="unit" id="sndmix"></span></label>
          <span><input class="val num" type="number" id="v_vol" min="0" max="100" step="1"
            aria-label="Volume"><span class="unit">%</span></span></div>
        <input type="range" id="r_vol" min="0" max="100" step="1" aria-label="Volume">
      </div>
      <div id="sndlist" class="empty">Loading the sound list&hellip;</div>
      <div class="acts"><button class="act" id="sndscan">Rescan folder</button></div>
      <p class="note" id="sndnote"></p>
    </details>
  </div>

  <!-- ============ tuning ============ -->
  <div class="col" data-col="tune">
    <details class="pane" id="p-cast-cfg">
      <summary>Casting thresholds <em>live, not saved</em></summary>
      <div id="knobs-cast"></div>
      <p class="note">Nothing here survives a restart. Once it feels right, put the numbers
        in <span class="num">deploy/systemd/campbus-staff.service</span>.</p>
    </details>

    <details class="pane" id="p-filter">
      <summary>Input filters <em id="filtsum">&ndash;</em></summary>
      <div style="font-size:13px;color:var(--dim);margin-bottom:2px">How to combine the window</div>
      <div class="seg" id="filtermode">
        <button data-v="off" aria-pressed="false">Off</button>
        <button data-v="mean" aria-pressed="false">Mean</button>
        <button data-v="median" aria-pressed="false">Median</button>
      </div>
      <p class="note" style="margin-bottom:12px">A mean spreads a spike across the
        window; a median throws it away, for the same lag and the same cost. Reach for
        mean only when a reading is noisy rather than spiky.</p>
      <div id="knobs-filter"></div>
      <p class="note">The window counts frames, not time, and this node samples faster
        while armed than at rest. Three frames is about 300&nbsp;ms of holding still but
        100&nbsp;ms of sweeping &mdash; patient while waiting, quick while reading.</p>
    </details>

    <details class="pane" id="p-gate-cfg">
      <summary>GPS motion gate <em id="gate">&ndash;</em></summary>
      <div id="knobs-gate"></div>
      <div class="row"><span>Still for</span><b class="num" id="stillfor">&ndash;</b></div>
      <div class="row"><span>GPS reader</span><b class="num" id="gpsstate">&ndash;</b></div>
      <div class="row"><span>Wake-ups</span><b class="num" id="trans">&ndash;</b></div>
    </details>
  </div>

  <!-- ============ system ============ -->
  <div class="col" data-col="sys">
    <details class="pane" id="p-head">
      <summary>Compass <em id="calsum">&ndash;</em></summary>
      <canvas id="dial" width="118" height="118"></canvas>
      <div class="row"><span>Heading</span><b class="num" id="hd">&ndash;</b></div>
      <div class="row"><span>Calibration</span><b class="num" id="cal">&ndash;</b></div>
      <div class="row"><span>Gravity</span><b class="num" id="grav">&ndash;</b></div>
      <p class="note">A cast carries a heading only while the magnetometer reads 3. A
        confidently wrong heading points the spiral the wrong way.</p>
    </details>
    <details class="pane" id="p-pos">
      <summary>Position <em id="fixsum">&ndash;</em></summary>
      <div class="row"><span>Fix</span><b class="num" id="fix">&ndash;</b></div>
      <div class="row"><span>Accuracy</span><b class="num" id="acc">&ndash;</b></div>
      <div class="row"><span>Satellites, HDOP</span><b class="num" id="sat">&ndash;</b></div>
      <div class="row"><span>Sentences</span><b class="num" id="sent">&ndash;</b></div>
    </details>
    <details class="pane" id="p-bus">
      <summary>Bus health <em id="bussum">&ndash;</em></summary>
      <div class="row"><span>IMU reads</span><b class="num" id="reads">&ndash;</b></div>
      <div class="row"><span>Corrupt frames</span><b class="num" id="badf">&ndash;</b></div>
      <div class="row"><span>IMU errors</span><b class="num" id="ioerr">&ndash;</b></div>
      <div class="row"><span>Bad checksums</span><b class="num" id="badck">&ndash;</b></div>
      <p class="note" id="bushint"></p>
    </details>
  </div>
  </div><!-- /side -->
</div>

<nav id="tabs" role="tablist">
  <button role="tab" data-col="log" aria-selected="true">Attempts</button>
  <button role="tab" data-col="tune" aria-selected="false">Tune</button>
  <button role="tab" data-col="sys" aria-selected="false">System</button>
  <button role="tab" data-col="snd" aria-selected="false">Spell</button>
</nav>

<script>
(() => {
  const $ = (id) => document.getElementById(id);
  const hist = [];
  let thresholds = { accel: 0.35, gyro: 15, trigger: 60 };
  let dragging = 0;
  let lastTrace = null, lastKey = '';
  let flash = null;
  const DEG = '\\u00b0', CWA = '\\u21bb', CCWA = '\\u21ba';
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
    del(k) { try { localStorage.removeItem(k); } catch (e) {} },
  };

  function connect() {
    const es = new EventSource('stream');
    es.onopen = () => { $('conn').textContent = 'live'; $('linkdot').className = 'dot on'; };
    es.onerror = () => { $('conn').textContent = 'reconnecting'; $('linkdot').className = 'dot off'; };
    es.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (err) {} };
  }
  // State changes are POST + a custom header (see the module docstring: no CSRF via GET).
  const post = (url) => fetch(url, { method: 'POST', headers: { 'X-Campbus': '1' } });
  const push = (url) => post(url).catch(() => {});
  const fmt = (v, n) => (v == null ? '-' : (+v).toFixed(n));
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  // ---- knobs: one slider and one box you can type in, generated from a spec so the
  //      two can never drift apart ------------------------------------------------
  const KNOBS = [
    ['cast', 'arm_seconds', 'Hold still for', 's', 0.5, 6, 0.1, 1],
    ['cast', 'arm_accel', 'Still means acceleration under', 'm/s\\u00b2', 0.05, 1.5, 0.01, 2],
    ['cast', 'arm_gyro', 'Still means rotation under', DEG + '/s', 1, 40, 0.5, 1],
    ['cast', 'trigger_dps', 'A sweep starts above', DEG + '/s', 20, 300, 5, 0],
    ['cast', 'min_deg', 'A sweep must cover', DEG, 10, 270, 5, 0],
    ['cast', 'dir_purity', 'Never doubles back', '', 0.1, 1, 0.05, 2],
    ['cast', 'axis_purity', 'Turns flat, does not swing', '', 0, 1, 0.05, 2],
    ['cast', 'sleep_after', 'Untouched this long, it sleeps', 's', 5, 300, 5, 0],
    ['filter', 'accel_filter', 'Acceleration window', 'frames', 1, 25, 1, 0],
    ['filter', 'gyro_filter', 'Rotation window', 'frames', 1, 25, 1, 0],
    ['gate', 'accel', 'Moving means acceleration over', 'm/s\\u00b2', 0.02, 2, 0.01, 2],
    ['gate', 'gyro', 'Moving means rotation over', DEG + '/s', 0.5, 60, 0.5, 1],
    ['gate', 'still', 'Still this long parks the GPS', 's', 2, 60, 1, 0],
  ];
  const DP = {}, LIM = {};

  KNOBS.forEach(([group, p, label, unit, min, max, step, dp]) => {
    DP[p] = dp; LIM[p] = [min, max];
    const d = document.createElement('div');
    d.className = 'knob';
    d.innerHTML =
      '<div class="lab"><label for="r_' + p + '">' + label + '</label>' +
      '<span><input class="val num" type="number" id="v_' + p + '" min="' + min +
      '" max="' + max + '" step="' + step + '" aria-label="' + label + '">' +
      (unit ? '<span class="unit">' + unit + '</span>' : '') + '</span></div>' +
      '<input type="range" id="r_' + p + '" min="' + min + '" max="' + max +
      '" step="' + step + '" aria-label="' + label + '">';
    $('knobs-' + group).appendChild(d);

    const range = $('r_' + p), box = $('v_' + p);
    const send = (v) => { dragging = Date.now(); push('config?' + p + '=' + v); };
    range.addEventListener('input', () => { dragging = Date.now(); box.value = (+range.value).toFixed(dp); });
    range.addEventListener('change', () => send(range.value));
    // typed values are free of the slider's step and range - clamp, do not silently ignore
    const commit = () => {
      const raw = parseFloat(box.value);
      if (!isFinite(raw)) { box.classList.add('bad'); return; }
      box.classList.remove('bad');
      const v = clamp(raw, min, max);
      box.value = v.toFixed(dp); range.value = v; send(v);
    };
    box.addEventListener('input', () => { dragging = Date.now(); });
    box.addEventListener('change', commit);
    box.addEventListener('keydown', (e) => { if (e.key === 'Enter') { commit(); box.blur(); } });
  });

  function setKnob(p, value) {
    if (value == null) return;
    const range = $('r_' + p), box = $('v_' + p);
    if (!range || box === document.activeElement) return;   // never fight the typist
    range.value = value; box.value = (+value).toFixed(DP[p]);
  }

  function setMode(v) {
    if (!v) return;
    document.querySelectorAll('#filtermode button').forEach((b) =>
      b.setAttribute('aria-pressed', String(b.dataset.v === v)));
  }
  document.querySelectorAll('#filtermode button').forEach((b) => {
    b.addEventListener('click', () => {
      dragging = Date.now(); setMode(b.dataset.v);
      push('config?filter_mode=' + b.dataset.v);
    });
  });

  // ---- collapsing, remembered per phone ---------------------------------------
  document.querySelectorAll('details.pane').forEach((d) => {
    const key = 'staffui.' + d.id;
    const saved = store.get(key);
    if (saved !== null) d.open = saved === '1';
    d.addEventListener('toggle', () => store.set(key, d.open ? '1' : '0'));
  });

  // ---- casting -----------------------------------------------------------------
  const BADGE = {
    idle:     ['Ready', 'Hold it still to arm'],
    asleep:   ['Asleep', 'Pick it up to wake it'],
    arming:   ['Hold still', ''],
    armed:    ['Armed', ''],
    capture:  ['Sweeping', 'Reading the turn'],
    cooldown: ['\\u2014', 'Settling'],
  };

  function renderCast(c, im) {
    if (!c || !c.present) {
      $('fstate').textContent = 'No casting';
      $('fsub').textContent = (c && c.why) || 'started without --cast';
      return;
    }
    const att = (c.attempts || [])[0];
    const key = att ? att.reason + '|' + att.deg + '|' + att.samples : '';
    if (att && key !== lastKey) {
      if (att.ago < 1.2) flash = { ok: att.ok, at: Date.now() };
      lastTrace = att; lastKey = key; renderLast(att, c);
    }

    let cls, big, sub, fill = 0;
    if (flash && Date.now() - flash.at < 1700 && lastTrace) {
      if (lastTrace.ok) {
        cls = 'st-cast';
        big = (lastTrace.spin_dir > 0 ? CWA + ' Cast clockwise' : CCWA + ' Cast counter-clockwise');
        sub = Math.abs(lastTrace.deg).toFixed(0) + DEG + ' at intensity ' + lastTrace.intensity;
      } else { cls = 'st-reject'; big = 'No cast'; sub = lastTrace.reason; }
      fill = 1;
    } else {
      const b = BADGE[c.state] || ['-', ''];
      cls = 'st-' + c.state; big = b[0]; sub = b[1];
      if (c.state === 'arming') {
        sub = fmt(c.held_for, 1) + ' of ' + fmt(c.cfg.arm_seconds, 1) + ' seconds';
        fill = c.arm_progress || 0;
      }
      if (c.state === 'armed') {
        sub = 'Sweep it \\u2014 ' + Math.max(0, c.cfg.arm_timeout - c.in_state).toFixed(0) + 's left';
        fill = 1;
      }
      if (c.state === 'capture') fill = 1;
    }
    $('face').className = cls;
    $('fstate').textContent = big;
    $('fsub').textContent = sub;
    $('ffill').style.width = (100 * fill) + '%';

    thresholds.trigger = c.cfg.trigger_dps;
    const span = Math.max(c.cfg.trigger_dps * 3, 120);
    const shown = Math.abs(c.yaw) < 0.5 ? 0 : c.yaw;   // a resting staff is not turning
    const frac = Math.min(1, Math.abs(shown) / span);
    $('ybar').hidden = shown === 0;
    $('ybar').style.width = (50 * frac) + '%';
    $('ybar').style.left = shown >= 0 ? '50%' : (50 - 50 * frac) + '%';
    $('ybar').className = shown >= 0 ? '' : 'ccw';
    $('yval').textContent = (shown > 0 ? '+' : '') + fmt(shown, 0) + DEG + '/s';
    $('yval').className = 'num' + (shown > 0 ? ' cw' : shown < 0 ? ' ccw' : '');
    const tf = 50 * Math.min(1, c.cfg.trigger_dps / span);
    $('ytp').style.left = (50 + tf) + '%';
    $('ytn').style.left = (50 - tf) + '%';

    const oka = im.accel <= c.cfg.arm_accel, okg = im.gyro <= c.cfg.arm_gyro;
    $('stacc').className = oka ? 'pass' : 'fail';
    $('stacc').textContent = fmt(im.accel, 2) + '/' + fmt(c.cfg.arm_accel, 2);
    $('stgyr').className = okg ? 'pass' : 'fail';
    $('stgyr').textContent = fmt(im.gyro, 1) + '/' + fmt(c.cfg.arm_gyro, 1);
    $('tally').textContent = c.casts + ', ' + c.rejects;

    if (!dragging || Date.now() - dragging > 1500) {
      ['arm_seconds', 'arm_accel', 'arm_gyro', 'trigger_dps', 'min_deg',
       'dir_purity', 'axis_purity', 'sleep_after'].forEach((p) => setKnob(p, c.cfg[p]));
    }
    renderLog(c.attempts || []);
    if (c.last) {
      $('payload').hidden = false;
      $('payload').textContent = 'prop/' + (c.last.payload.prop_id || '') + '  ' +
        JSON.stringify(c.last.payload);
    }
  }

  function renderLast(a, c) {
    const head = a.ok
      ? '<b style="color:' + (a.spin_dir > 0 ? 'var(--cw)' : 'var(--ccw)') + '">' +
        (a.spin_dir > 0 ? 'Cast clockwise' : 'Cast counter-clockwise') + '</b>'
      : '<b class="fail">Rejected</b> <span class="quiet">' + a.reason + '</span>';
    $('lastverdict').className = '';
    $('lastverdict').innerHTML = head +
      '<div class="stats">net ' + a.deg + DEG + '  path ' + a.abs_deg + DEG +
      '  peak ' + a.peak_dps + DEG + '/s  ' + a.dur + 's  ' + a.samples +
      ' samples at ' + a.hz + ' Hz<br>one-way ' + a.dir_purity + ' (needs ' + c.cfg.dir_purity +
      ')   flat ' + a.axis_purity + ' (needs ' + c.cfg.axis_purity + ')' +
      (a.ok ? '   intensity ' + a.intensity : '') + '</div>';
    $('tchart').hidden = false;
  }

  function renderLog(atts) {
    if (!atts.length) return;
    $('log').className = '';
    $('log').innerHTML = atts.map((a) => {
      const dir = a.ok ? (a.spin_dir > 0 ? 'cw' : 'ccw') : 'no';
      const head = a.ok ? (a.spin_dir > 0 ? CWA + ' clockwise' : CCWA + ' counter-clockwise')
                        : 'rejected';
      const why = a.ok ? Math.abs(a.deg).toFixed(0) + DEG + ' at intensity ' + a.intensity
                       : a.reason;
      return '<div class="att"><div class="top">' +
        '<span class="dir ' + dir + '">' + head + '</span>' +
        '<span class="why">' + why + '</span>' +
        '<span class="when num">' + a.ago + 's</span></div>' +
        '<div class="stats">path ' + a.abs_deg + DEG + '  peak ' + a.peak_dps +
        '  one-way ' + a.dir_purity + '  flat ' + a.axis_purity +
        '  ' + a.samples + ' at ' + a.hz + ' Hz</div></div>';
    }).join('');
  }

  // ---- attitude ----------------------------------------------------------------
  const qmul = (a, b) => [
    a[0]*b[0] - a[1]*b[1] - a[2]*b[2] - a[3]*b[3],
    a[0]*b[1] + a[1]*b[0] + a[2]*b[3] - a[3]*b[2],
    a[0]*b[2] - a[1]*b[3] + a[2]*b[0] + a[3]*b[1],
    a[0]*b[3] + a[1]*b[2] - a[2]*b[1] + a[3]*b[0]];
  const qconj = (q) => [q[0], -q[1], -q[2], -q[3]];
  const qrot = (q, v) => {
    const r = qmul(qmul(q, [0, v[0], v[1], v[2]]), qconj(q));
    return [r[1], r[2], r[3]];
  };

  let qTarget = [1, 0, 0, 0], qNow = [1, 0, 0, 0], qZero = null;
  const savedZero = store.get('staffui.qzero');
  if (savedZero) { try { qZero = JSON.parse(savedZero); } catch (e) {} }
  $('orientmode').textContent = qZero ? 'zeroed' : 'absolute';

  $('zero').addEventListener('click', () => {
    qZero = qTarget.slice();
    store.set('staffui.qzero', JSON.stringify(qZero));
    $('orientmode').textContent = 'zeroed';
  });
  $('unzero').addEventListener('click', () => {
    qZero = null; store.del('staffui.qzero');
    $('orientmode').textContent = 'absolute';
  });

  // The feed is 10 Hz; the eye wants 60. Ease toward the latest attitude instead of
  // stepping to it, or a slow turn looks like a stutter rather than a turn.
  function qstep() {
    let t = qTarget;
    if (qNow[0]*t[0] + qNow[1]*t[1] + qNow[2]*t[2] + qNow[3]*t[3] < 0) t = t.map((v) => -v);
    const n = qNow.map((v, i) => v + (t[i] - v) * 0.3);
    const m = Math.hypot(n[0], n[1], n[2], n[3]) || 1;
    qNow = n.map((v) => v / m);
  }

  // A square shaft and a faceted head. The square section is not decoration: a round
  // rod gives you no way to see rotation about its own axis.
  const MODEL = (() => {
    const P = [], E = [], add = (p) => (P.push(p), P.length - 1);
    const w = 0.075, top = 0.95, bot = -1.2;
    const ring = (z) => [add([-w, -w, z]), add([w, -w, z]), add([w, w, z]), add([-w, w, z])];
    const a = ring(bot), b = ring(top);
    for (let i = 0; i < 4; i++) {
      E.push([a[i], a[(i + 1) % 4], 0]);
      E.push([b[i], b[(i + 1) % 4], 0]);
      E.push([a[i], b[i], 0]);
    }
    const r = 0.25, cz = top + r * 0.85;
    const o = [add([r, 0, cz]), add([-r, 0, cz]), add([0, r, cz]), add([0, -r, cz]),
               add([0, 0, cz + r]), add([0, 0, cz - r])];
    [[0,2],[2,1],[1,3],[3,0],[0,4],[2,4],[1,4],[3,4],[0,5],[2,5],[1,5],[3,5]]
      .forEach(([i, j]) => E.push([o[i], o[j], 1]));
    return { P, E, tip: [0, 0, top + r * 1.85], foot: [0, 0, bot] };
  })();

  const V_YAW = -0.6, V_PITCH = 0.38, FOCAL = 5.4, FLOOR = -1.45;
  function project(p, w, h) {
    const cy = Math.cos(V_YAW), sy = Math.sin(V_YAW);
    const X = p[0] * cy - p[1] * sy, Y = p[0] * sy + p[1] * cy, Z = p[2];
    const cp = Math.cos(V_PITCH), sp = Math.sin(V_PITCH);
    const depth = Y * cp + Z * sp, up = -Y * sp + Z * cp;
    const s = FOCAL / (FOCAL + depth), k = h * 0.27;
    return [w / 2 + X * s * k, h / 2 - up * s * k, depth];
  }

  function drawScene() {
    const f = fit($('scene')); if (!f) return;
    const [ctx, w, h] = f;
    qstep();
    const q = qZero ? qmul(qNow, qconj(qZero)) : qNow;
    const seg = (p1, p2, col, lw) => {
      const a = project(p1, w, h), b = project(p2, w, h);
      ctx.strokeStyle = col; ctx.lineWidth = lw || 1;
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
    };

    // the world, holding still while the staff moves in it
    const G = 1.45, N = 6;
    for (let i = 0; i <= N; i++) {
      const t = -G + 2 * G * i / N;
      seg([t, -G, FLOOR], [t, G, FLOOR], '#1b2831');
      seg([-G, t, FLOOR], [G, t, FLOOR], '#1b2831');
    }

    const pts = MODEL.P.map((p) => project(qrot(q, p), w, h));
    // where it stands, and which way it leans
    const tip = qrot(q, MODEL.tip), foot = qrot(q, MODEL.foot);
    seg([tip[0], tip[1], FLOOR], [foot[0], foot[1], FLOOR], '#22323a', 2);
    seg([foot[0], foot[1], foot[2]], [foot[0], foot[1], FLOOR], '#1a262c');

    const edges = MODEL.E.map(([i, j, kind]) => ({
      i, j, kind, d: (pts[i][2] + pts[j][2]) / 2,
    })).sort((a, b) => b.d - a.d);          // far edges first, so near ones sit on top
    edges.forEach((e) => {
      const near = 1 - Math.min(1, Math.max(0, (e.d + 1.4) / 2.8));
      ctx.strokeStyle = e.kind ? 'rgba(255,162,61,' + (0.45 + 0.55 * near).toFixed(2) + ')'
                              : 'rgba(150,175,186,' + (0.3 + 0.55 * near).toFixed(2) + ')';
      ctx.lineWidth = e.kind ? 1.6 : 1.3;
      ctx.beginPath(); ctx.moveTo(pts[e.i][0], pts[e.i][1]);
      ctx.lineTo(pts[e.j][0], pts[e.j][1]); ctx.stroke();
    });
  }

  // ---- everything else ---------------------------------------------------------
  function render(s) {
    const im = s.imu || {}, gp = s.gps || {}, ga = s.gate || {};
    renderSound(s.sound);          // before the IMU check: sound works with no IMU
    renderKnob(s.knob);
    if (s.node) {
      $('nodeid').textContent = s.node.id || 'staff';
      $('broker').textContent = s.node.connected ? 'on the bus' : 'broker offline';
      $('broker').className = s.node.connected ? '' : 'fail';
    }
    if (!im.present) {
      $('fstate').textContent = 'No IMU';
      $('fsub').textContent = im.why || '';
      return;
    }
    $('hz').textContent = fmt(im.hz, 1) + ' Hz';
    renderCast(s.cast, im);

    if (im.quat && Math.abs(im.quat[0]) + Math.abs(im.quat[1]) +
        Math.abs(im.quat[2]) + Math.abs(im.quat[3]) > 0.5) qTarget = im.quat;
    $('o_head').textContent = im.heading == null ? '-' : fmt(im.heading, 1) + DEG;
    $('o_roll').textContent = im.roll == null ? '-' : fmt(im.roll, 1) + DEG;
    $('o_pitch').textContent = im.pitch == null ? '-' : fmt(im.pitch, 1) + DEG;

    $('state').textContent = im.moving ? 'moving' : 'still';
    $('av').textContent = fmt(im.accel, 2);
    $('gv').textContent = fmt(im.gyro, 1);
    // show what the filter took off, so you can watch it work rather than trust it
    const filtering = im.filter_mode !== 'off' && (im.accel_filter > 1 || im.gyro_filter > 1);
    $('avr').textContent = filtering ? 'raw ' + fmt(im.accel_raw, 2) : '';
    $('gvr').textContent = filtering ? 'raw ' + fmt(im.gyro_raw, 1) : '';
    $('filtsum').textContent = im.filter_mode === 'off' ? 'off'
      : im.filter_mode + ' ' + im.accel_filter + '/' + im.gyro_filter;
    const aMax = Math.max(im.accel_thresh * 2.5, 0.1), gMax = Math.max(im.gyro_thresh * 2.5, 1);
    $('af').style.width = Math.min(100, 100 * im.accel / aMax) + '%';
    $('gf').style.width = Math.min(100, 100 * im.gyro / gMax) + '%';
    $('af').className = 'fill' + (im.accel > im.accel_thresh ? ' over' : '');
    $('gf').className = 'fill' + (im.gyro > im.gyro_thresh ? ' over' : '');
    $('at').style.left = Math.min(100, 100 * im.accel_thresh / aMax) + '%';
    $('gt').style.left = Math.min(100, 100 * im.gyro_thresh / gMax) + '%';

    if (!dragging || Date.now() - dragging > 1500) {
      thresholds.accel = im.accel_thresh; thresholds.gyro = im.gyro_thresh;
      setKnob('accel', im.accel_thresh);
      setKnob('gyro', im.gyro_thresh);
      setKnob('still', ga.still_seconds);
      setKnob('accel_filter', im.accel_filter);
      setKnob('gyro_filter', im.gyro_filter);
      setMode(im.filter_mode);
    }

    hist.push({ a: im.accel, g: im.gyro, ar: im.accel_raw, gr: im.gyro_raw,
                y: (s.cast && s.cast.yaw) || 0 });
    while (hist.length > 400) hist.shift();

    $('gate').innerHTML = ga.active
      ? (ga.idle ? '<span style="color:var(--cw)">parked, holding</span>' : 'tracking')
      : 'off';
    $('stillfor').textContent = fmt(im.still_for, 1) + ' s';
    $('gpsstate').textContent = gp.paused ? 'parked' : (gp.present ? 'reading' : 'absent');
    $('trans').textContent = ga.transitions != null ? ga.transitions : '-';

    $('hd').textContent = im.heading == null ? '-' : fmt(im.heading, 1) + DEG;
    const cal = im.calib || [0, 0, 0, 0];
    $('cal').textContent = cal.join('/');
    $('calsum').innerHTML = cal[3] < 3
      ? '<span class="fail">no heading sent</span>' : 'heading trusted';
    $('grav').textContent = (im.gravity || []).map((v) => (+v).toFixed(1)).join('  ');
    drawDial(im.heading, cal[3]);

    const f = gp.fix;
    $('fix').textContent = f ? f.lat.toFixed(6) + ', ' + f.lon.toFixed(6)
                             : (gp.present ? 'none' : 'absent');
    $('fixsum').textContent = f ? '\\u00b1' + f.acc_m + ' m' : (gp.present ? 'no fix' : 'absent');
    $('acc').textContent = f ? '\\u00b1' + f.acc_m + ' m' : '-';
    $('sat').textContent = f ? f.sats + ', ' + f.hdop : '-';
    $('sent').textContent = gp.sentences != null ? gp.sentences : '-';

    $('reads').textContent = im.reads;
    const pct = im.bad_pct || 0;
    $('badf').innerHTML = im.bad_frames + ' <span class="' + (pct > 2 ? 'fail' : 'quiet') + '">'
      + pct.toFixed(1) + '%</span>';
    $('ioerr').textContent = im.io_errors;
    $('badck').textContent = gp.bad_checksums != null ? gp.bad_checksums : '-';
    $('bussum').innerHTML = pct > 2 ? '<span class="fail">' + pct.toFixed(1) + '% corrupt</span>'
                                    : 'healthy';
    $('bushint').textContent = pct > 2
      ? 'Corrupt frames are the Pi mishandling this chip I2C clock stretching. They are '
        + 'dropped, not believed, but a high rate means the bus needs slowing further.'
      : 'Bus looks healthy.';
  }

  // ---- charts ------------------------------------------------------------------
  function fit(cv) {
    const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    if (!r.width || !r.height) return null;
    if (cv.width !== Math.round(r.width * dpr) || cv.height !== Math.round(r.height * dpr)) {
      cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr);
    }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, r.width, r.height);
    return [ctx, r.width, r.height];
  }

  function drawChart() {
    const f = fit($('chart')); if (!f || hist.length < 2) return;
    const [ctx, w, h] = f;
    const aMax = Math.max(thresholds.accel * 2.5, ...hist.map((p) => p.a), 0.1);
    const gMax = Math.max(thresholds.gyro * 2.5, ...hist.map((p) => p.g), 1);
    const x = (i) => w * i / (hist.length - 1);
    ctx.strokeStyle = '#41535a'; ctx.setLineDash([2, 3]); ctx.lineWidth = 1;
    const ty = h - h * thresholds.accel / aMax;
    ctx.beginPath(); ctx.moveTo(0, ty); ctx.lineTo(w, ty); ctx.stroke(); ctx.setLineDash([]);
    const line = (key, max, col, lw) => {
      ctx.strokeStyle = col; ctx.lineWidth = lw || 1.4; ctx.beginPath();
      hist.forEach((p, i) => {
        const v = p[key]; if (v == null) return;
        const y = h - h * v / max; i ? ctx.lineTo(x(i), y) : ctx.moveTo(x(i), y);
      });
      ctx.stroke();
    };
    // raw underneath, filtered on top: the gap between them is the filter's work
    line('ar', aMax, 'rgba(95,184,255,.26)', 1);
    line('gr', gMax, 'rgba(143,163,171,.26)', 1);
    line('a', aMax, '#5fb8ff');
    line('g', gMax, '#8fa3ab');
    ctx.font = '10px ui-monospace,monospace';
    ctx.fillStyle = '#5fb8ff'; ctx.fillText('accel', 5, 12);
    ctx.fillStyle = '#8fa3ab'; ctx.fillText('rotation', 44, 12);
    ctx.fillStyle = '#41535a'; ctx.fillText('(pale = unfiltered)', 100, 12);
  }

  // Signed turn rate with zero in the middle. A good sweep is one clean hump on one
  // side of the line; a shake crosses it.
  function drawYaw() {
    const f = fit($('ychart')); if (!f || hist.length < 2) return;
    const [ctx, w, h] = f;
    const span = Math.max(thresholds.trigger * 3, 120, ...hist.map((p) => Math.abs(p.y)));
    const mid = h / 2, x = (i) => w * i / (hist.length - 1);
    const yOf = (v) => mid - (mid - 3) * v / span;
    ctx.strokeStyle = '#2a3a42'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, mid + .5); ctx.lineTo(w, mid + .5); ctx.stroke();
    ctx.strokeStyle = '#41535a'; ctx.setLineDash([2, 3]);
    [1, -1].forEach((sgn) => {
      const ty = yOf(sgn * thresholds.trigger);
      ctx.beginPath(); ctx.moveTo(0, ty); ctx.lineTo(w, ty); ctx.stroke();
    });
    ctx.setLineDash([]);
    // Above the line is clockwise, below is counter-clockwise, and near it is neither -
    // without that deadband a resting staff strobes amber/blue across the middle.
    const dead = Math.max(4, span * 0.04);
    const hue = (v) => (v > dead ? '#ffa23d' : v < -dead ? '#5fb8ff' : '#3c4c54');
    ctx.lineWidth = 1.5;
    for (let i = 1; i < hist.length; i++) {
      ctx.beginPath(); ctx.moveTo(x(i - 1), yOf(hist[i - 1].y)); ctx.lineTo(x(i), yOf(hist[i].y));
      ctx.strokeStyle = hue(hist[i].y); ctx.stroke();
    }
    ctx.font = '10px ui-monospace,monospace'; ctx.fillStyle = '#41535a';
    ctx.fillText('+' + Math.round(span), 5, 11);
    ctx.fillText('-' + Math.round(span), 5, h - 4);
  }

  // The captured window on its own, so a rejected attempt can still be read after the
  // fact. The shaded area under the curve is literally the degrees turned.
  function drawTrace() {
    const cv = $('tchart'); if (cv.hidden) return;
    const f = fit(cv); if (!f) return;
    const [ctx, w, h] = f;
    const tr = lastTrace && lastTrace.trace;
    if (!tr || tr.length < 2) return;
    const span = Math.max(thresholds.trigger * 1.5, 60, ...tr.map((p) => Math.abs(p[1])));
    const dur = tr[tr.length - 1][0] || 1, mid = h / 2;
    const yOf = (v) => mid - (mid - 4) * v / span;
    const xOf = (t) => w * t / dur;
    ctx.strokeStyle = '#2a3a42'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, mid + .5); ctx.lineTo(w, mid + .5); ctx.stroke();
    ctx.beginPath();
    tr.forEach((p, i) => { const y = yOf(p[1]); i ? ctx.lineTo(xOf(p[0]), y) : ctx.moveTo(xOf(p[0]), y); });
    const col = lastTrace.ok ? (lastTrace.spin_dir > 0 ? '#ffa23d' : '#5fb8ff') : '#ff5c4d';
    ctx.strokeStyle = col; ctx.lineWidth = 1.7; ctx.stroke();
    ctx.lineTo(w, mid); ctx.lineTo(0, mid); ctx.closePath();
    ctx.globalAlpha = .16; ctx.fillStyle = col; ctx.fill(); ctx.globalAlpha = 1;
    ctx.font = '10px ui-monospace,monospace'; ctx.fillStyle = '#41535a';
    ctx.fillText(dur.toFixed(2) + 's', 5, h - 4);
  }

  function drawDial(heading, magcal) {
    const f = fit($('dial')); if (!f) return;
    const [x, w, h] = f;
    const s = Math.min(w, h), cx = w / 2, cy = h / 2, r = s / 2 - 13;
    x.strokeStyle = '#1c272d'; x.lineWidth = 1.5;
    x.beginPath(); x.arc(cx, cy, r, 0, Math.PI * 2); x.stroke();
    x.strokeStyle = '#2a3a42';
    for (let i = 0; i < 12; i++) {                    // an instrument has divisions
      const a = i * Math.PI / 6 - Math.PI / 2, inner = i % 3 === 0 ? r - 7 : r - 4;
      x.beginPath();
      x.moveTo(cx + Math.cos(a) * inner, cy + Math.sin(a) * inner);
      x.lineTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
      x.stroke();
    }
    x.fillStyle = '#41535a'; x.font = '10px system-ui'; x.textAlign = 'center';
    ['N', 'E', 'S', 'W'].forEach((d, i) => {
      const a = i * Math.PI / 2 - Math.PI / 2;
      x.fillText(d, cx + Math.cos(a) * (r + 7), cy + Math.sin(a) * (r + 7) + 3.5);
    });
    if (heading == null) return;
    const a = (heading - 90) * Math.PI / 180;
    x.strokeStyle = magcal < 3 ? '#41535a' : '#ffa23d'; x.lineWidth = 2.5;
    x.beginPath(); x.moveTo(cx - Math.cos(a) * r * .22, cy - Math.sin(a) * r * .22);
    x.lineTo(cx + Math.cos(a) * r * .82, cy + Math.sin(a) * r * .82); x.stroke();
  }

  function loop() {
    try { drawChart(); drawYaw(); drawTrace(); drawScene(); } catch (e) {}
    requestAnimationFrame(loop);
  }

  // ---- spell knob: read-only, the knob on the staff is the control ----------------
  function renderKnob(k) {
    if (!k) return;
    $('kfill').style.background = k.color || 'transparent';
    if (!k.present) {
      $('ksum').textContent = 'no knob';
      $('kfill').style.opacity = 0.25;
      $('khue').textContent = '-'; $('klevel').textContent = '-';
      $('kbtn').textContent = '-'; $('kstats').textContent = '-';
      $('knote').textContent = 'No knob: ' + (k.why || 'absent') + '. Casts use ' + (k.color || '--color') + '.';
      return;
    }
    $('ksum').innerHTML = k.ok ? esc(k.color) : '<span class="fail">not answering</span>';
    // mirror the pixel, but never fully dark, or a resting glow reads as "off"
    $('kfill').style.opacity = Math.max(0.12, k.level);
    $('khue').textContent = fmt(k.hue, 0) + DEG + '  ' + k.color;
    $('klevel').textContent = Math.round(100 * k.level) + '%' + (k.intro ? '  rainbow' : '') +
      '  (rest ' + Math.round(100 * k.rest) + '%)';
    $('kbtn').textContent = k.pressed ? 'pressed' : 'up';
    $('kstats').textContent = k.turns + ', ' + k.presses + ', ' + k.io_errors;
  }

  // ---- sound: the page asks, sound.py plays ------------------------------------
  // The list is fetched, not streamed: it only changes when someone copies files
  // onto the Pi, and Rescan is right there. The stream carries what is playing.
  let sndPlaying = null, volDrag = 0, sndWas = null;
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const sndLabel = (rel) => rel.split('/').pop().replace(/\\.wav$/i, '');
  const sndAsk = (url) => post(url).then((r) => r.json()).then(renderSound).catch(() => {});

  function loadSounds() {
    fetch('sound/list').then((r) => r.json()).then((d) => {
      renderSoundList(d); renderSound(d);
    }).catch(() => { $('sndlist').className = 'empty';
                     $('sndlist').textContent = 'Could not load the sound list.'; });
  }

  function renderSoundList(d) {
    const box = $('sndlist'), files = d.files || [];
    $('sndscan').disabled = !d.present;
    if (!d.present) { box.className = 'empty'; box.textContent = 'No sound here: ' + (d.why || 'unavailable'); return; }
    if (!files.length) { box.className = 'empty'; box.textContent = 'No .wav files under ' + d.dir; return; }
    const groups = {};
    files.forEach((f) => {
      const i = f.lastIndexOf('/'), g = i < 0 ? '' : f.slice(0, i);
      (groups[g] = groups[g] || []).push(f);
    });
    box.className = '';
    box.innerHTML = Object.keys(groups).sort().map((g) =>
      (g ? '<div class="sgrp">' + esc(g) + '/</div>' : '') + '<div class="sgrid">' +
      groups[g].map((f) => '<button class="act" data-f="' + esc(f) + '" title="' + esc(f) +
        '">' + esc(sndLabel(f)) + '</button>').join('') + '</div>').join('');
    box.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
      sndPlaying = b.dataset.f; markPlaying();
      sndAsk('sound/play?f=' + encodeURIComponent(b.dataset.f));
    }));
    markPlaying();
  }

  function markPlaying() {
    $('sndlist').querySelectorAll('button').forEach((b) =>
      b.classList.toggle('on', b.dataset.f === sndPlaying));
  }

  function renderSound(sd) {
    if (!sd) return;
    // the card can turn up after the page loaded (the driver probes late at boot):
    // fetch the list then, or the panel would keep saying there is no sound
    if (sd.present && sndWas === false) { sndWas = true; loadSounds(); }
    sndWas = !!sd.present;
    sndPlaying = sd.playing || null;
    $('sndsum').textContent = !sd.present ? 'no card' : sndPlaying ? 'playing'
      : (sd.count != null ? sd.count + ' sounds' : '');
    $('sndnow').className = sndPlaying ? 'on' : '';
    $('sndname').textContent = sndPlaying ? sndLabel(sndPlaying)
      : (sd.present ? 'nothing playing' : (sd.why || 'no sound'));
    $('sndname').title = sndPlaying || '';
    $('sndel').textContent = sndPlaying && sd.elapsed != null ? fmt(sd.elapsed, 1) + 's' : '';
    $('sndstop').disabled = !sndPlaying;
    markPlaying();
    const volOk = !!sd.present && sd.volume != null;
    $('r_vol').disabled = $('v_vol').disabled = !volOk;
    $('sndmix').textContent = sd.mixer ? sd.mixer : '';
    if (volOk && (!volDrag || Date.now() - volDrag > 1500) &&
        document.activeElement !== $('v_vol')) {
      $('r_vol').value = sd.volume; $('v_vol').value = sd.volume;
    }
    $('sndnote').className = 'note' + (sd.error ? ' fail' : '');
    $('sndnote').textContent = sd.error ? 'Last error: ' + sd.error
      : sd.follows_casts ? 'A cast also plays its sound (--sound-map).'
      : 'Casts are silent. --sound-map ties a sound to a gesture.';
  }

  const setVol = (v) => {
    v = clamp(Math.round(v), 0, 100);
    volDrag = Date.now(); $('r_vol').value = v; $('v_vol').value = v;
    sndAsk('sound/volume?v=' + v);
  };
  $('r_vol').addEventListener('input', () => { volDrag = Date.now(); $('v_vol').value = $('r_vol').value; });
  $('r_vol').addEventListener('change', () => setVol(+$('r_vol').value));
  $('v_vol').addEventListener('input', () => { volDrag = Date.now(); });
  $('v_vol').addEventListener('change', () => {
    const v = parseFloat($('v_vol').value);
    if (isFinite(v)) { $('v_vol').classList.remove('bad'); setVol(v); } else $('v_vol').classList.add('bad');
  });
  $('v_vol').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('v_vol').blur(); });
  $('sndstop').addEventListener('click', () => sndAsk('sound/stop'));
  $('sndscan').addEventListener('click', loadSounds);

  // ---- tabs (phones; the wide layout shows every column at once) ----------------
  document.querySelectorAll('#tabs button').forEach((b) => {
    b.addEventListener('click', () => {
      document.querySelectorAll('#tabs button').forEach((o) =>
        o.setAttribute('aria-selected', String(o === b)));
      document.body.dataset.tab = b.dataset.col;
    });
  });

  connect();
  loadSounds();
  loop();
})();
</script></body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "campbus-staff/0.4"
    state_fn: Callable[[], dict] = staticmethod(dict)
    config_fn: Optional[Callable[[dict], None]] = None
    sound = None                  # a sound.SoundPlayer, or None for "no sound here"

    def log_message(self, fmt, *args):
        pass          # 10 requests a second in journald helps nobody

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj) -> None:
        self._send(200, json.dumps(obj).encode(), "application/json")

    def _sound(self, path: str):
        """GET /sound/list; POST /sound/play?f=<rel>, /sound/stop, /sound/volume?v=0-100.

        Every one answers with the player's state, so the page updates from the reply
        instead of waiting a stream frame. Only files the folder scan found can be
        played - `f` is looked up in that list, never joined onto a path blindly."""
        snd = self.sound
        if snd is None:
            return self._json({"present": False, "why": "no sound player", "files": []})
        q = parse_qs(urlparse(self.path).query)
        if path == "/sound/list":
            return self._json(snd.listing())
        if path == "/sound/play":
            ok = snd.play(q.get("f", [""])[0])
            return self._json(dict(snd.state(), ok=ok))
        if path == "/sound/stop":
            snd.stop()
            return self._json(snd.state())
        if path == "/sound/volume":
            try:
                snd.set_volume(float(q.get("v", [""])[0]))
            except ValueError:
                pass
            return self._json(snd.state())
        return self._send(404, b"not found", "text/plain")

    # Routes that change state. They answer POST only (never GET, so a link or an <img>
    # cannot trigger them) and require the X-Campbus header the page sends.
    STATE_CHANGING = ("/config", "/sound/play", "/sound/stop", "/sound/volume")

    def do_GET(self):
        path = urlparse(self.path).path
        if path in self.STATE_CHANGING:
            return self._send(405, b"use POST", "text/plain")
        if path == "/sound/list":
            return self._sound(path)
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/state":
            return self._send(200, json.dumps(self.state_fn()).encode(), "application/json")
        if path == "/stream":
            return self._stream()
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        if 0 < n <= 65536:
            self.rfile.read(n)            # parameters ride in the query string; drain any body
        if path not in self.STATE_CHANGING:
            return self._send(404, b"not found", "text/plain")
        if self.headers.get("X-Campbus") != "1":
            return self._send(403, b"missing X-Campbus header", "text/plain")
        if path.startswith("/sound/"):
            return self._sound(path)
        if path == "/config":
            q = parse_qs(urlparse(self.path).query)
            patch = {}
            for key in CONFIG_FLOATS:
                if key in q:
                    try:
                        patch[key] = float(q[key][0])
                    except ValueError:
                        pass
            for key, allowed in CONFIG_WORDS.items():
                if key in q and q[key][0] in allowed:
                    patch[key] = q[key][0]
            if patch and self.config_fn:
                self.config_fn(patch)
            return self._send(200, json.dumps({"ok": True, "applied": patch}).encode(),
                              "application/json")
        self._send(404, b"not found", "text/plain")

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                payload = json.dumps(self.state_fn())
                self.wfile.write(b"data: " + payload.encode() + b"\n\n")
                self.wfile.flush()
                # 10 Hz is plenty for an eye and keeps the ARMv6 free for the sensors.
                # The gesture itself is decided in the IMU thread at --cast-hz, so what
                # the page misses between frames never changes what gets cast. The
                # orientation view eases between these frames rather than stepping.
                if self._stop.wait(0.1):
                    return
        except (BrokenPipeError, ConnectionResetError):
            return          # phone locked, tab closed, walked out of range: all normal


def start_web(port: int, state_fn: Callable[[], dict],
              config_fn: Optional[Callable[[dict], None]] = None, sound=None,
              host: str = "127.0.0.1"):
    """Serve the sensor page in a background thread. Returns the server.

    Binds 127.0.0.1 unless told otherwise; pass host="0.0.0.0" (staff_node.py --lan) to
    serve phones on the camp Wi-Fi."""
    stop = threading.Event()
    handler = type("_BoundHandler", (_Handler,), {
        "state_fn": staticmethod(state_fn),
        "config_fn": staticmethod(config_fn) if config_fn else None,
        "sound": sound,
        "_stop": stop,
    })
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
    httpd._campbus_stop = stop
    return httpd
