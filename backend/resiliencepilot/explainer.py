"""Read-only 'Day 1 Explainer' report: one standalone HTML file built only from recorded data.

Inputs (read-only): backend/data/runs/*.json (fallback reports/day1/), reports/day1/baseline-*.json,
config/dependency_map.json, config/safety.json. Never touches Docker or the safety controller.
"""
from __future__ import annotations

import json
from pathlib import Path

from .observation import utcnow


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def load_runs(root: Path) -> dict[str, tuple[dict, str]]:
    """run_id -> (record, source path). backend/data/runs wins over the committed copy in reports/day1."""
    runs: dict[str, tuple[dict, str]] = {}
    for d in (root / "reports" / "day1", root / "backend" / "data" / "runs"):
        for f in sorted(d.glob("2*.json")) if d.exists() else []:
            rec = json.loads(f.read_text(encoding="utf-8"))
            if "run_id" in rec and "timeline" in rec:
                runs[rec["run_id"]] = (rec, _rel(f, root))
    return dict(sorted(runs.items()))


def _events(rec: dict) -> dict:
    """Event times in seconds relative to verified injection (fault_active), or None."""
    tl = {}
    for e in rec["timeline"]:
        if e.get("t_s") is not None and e["event"] not in tl:
            tl[e["event"]] = e["t_s"]
    inj = tl.get("fault_active")
    rel = lambda ev: None if inj is None or tl.get(ev) is None else round(tl[ev] - inj, 3)  # noqa: E731
    return {
        "injected": 0.0 if inj is not None else None,
        "stale_detected": rec["impact"].get("time_to_detection_s"),
        "fault_window_closed": rel("fault_window_closed"),
        "rollback_verified": rel("rollback_result") if rec["rollback"].get("verified") else None,
        "recovery_verified": rel("recovery_verified"),
        "inject_t_s": inj,
    }


def _summary(rec: dict, src: str) -> dict:
    return {
        "run_id": rec["run_id"], "source": src, "state": rec["state"], "stop_reason": rec["stop_reason"],
        "duration_s": rec["request"]["duration_s"], "approved_by": rec["request"]["approved_by"],
        "impact": rec["impact"], "rollback": rec["rollback"], "recovery": rec["recovery"], "events": _events(rec),
        "limits": rec.get("limits", {}),
    }


def _is_full(rec: dict) -> bool:
    """Full-length telemetry_stall runs: the Day-1 explainer is about that experiment."""
    return (rec["state"] == "COMPLETED" and rec["stop_reason"] == "duration_elapsed"
            and rec["request"].get("experiment_type", "telemetry_stall") == "telemetry_stall")


def collect(root: Path, run_id: str | None) -> dict:
    runs = load_runs(root)
    if not runs:
        raise SystemExit("no run records found in backend/data/runs or reports/day1")
    if run_id:
        if run_id not in runs:
            raise SystemExit(f"run {run_id} not found")
        rec, src = runs[run_id]
    else:
        full = [rid for rid, (r, _) in runs.items() if _is_full(r)]
        if not full:
            raise SystemExit("no full-length COMPLETED run recorded")
        rec, src = runs[full[-1]]

    ev = _events(rec)
    inj = ev["inject_t_s"]
    observations = [{
        "t": None if inj is None else round(o["t_s"] - inj, 3), "phase": o["phase"],
        "http": o["observation"]["http_status"], "created": o["observation"]["created_at"],
        "received": o["observation"]["received_at"], "age": o["assessment"]["data_age_s"],
        "freshness": o["assessment"]["freshness"], "healthy_but_stale": o["assessment"]["healthy_but_stale"],
    } for o in rec["observations"]]

    estop = [(r, s) for r, s in runs.values() if r["stop_reason"] == "emergency_stop"]
    latched = [(r, s) for r, s in runs.values() if r["state"] == "LATCHED_UNSAFE"]

    baselines = []
    bdir = root / "reports" / "day1"
    for f in sorted(bdir.glob("baseline-*.json")):
        baselines.append((json.loads(f.read_text(encoding="utf-8"))["summary"], _rel(f, root)))
    with_load = [b for b in baselines if b[0].get("load_conditions")]
    ref = with_load[-1] if with_load else (baselines[-1] if baselines else (None, None))
    first = baselines[0] if baselines else (None, None)

    safety_cfg = json.loads((root / "config" / "safety.json").read_text(encoding="utf-8"))
    dep = json.loads((root / "config" / "dependency_map.json").read_text(encoding="utf-8"))

    return {
        "generated_at": utcnow().isoformat(),
        "run": {**_summary(rec, src), "request": rec["request"], "observations": observations},
        "full_runs": [_summary(r, s) for r, s in runs.values() if _is_full(r)],
        "estop_run": _summary(*estop[-1]) if estop else None,
        "estop_count": len(estop),
        "latched_run": _summary(*latched[-1]) if latched else None,
        "baseline_ref": {"summary": ref[0], "source": ref[1]},
        "baseline_first": {"summary": first[0], "source": first[1]},
        "safety_config": {"max_duration_s": safety_cfg.get("max_duration_s"),
                          "watchdog_grace_s": safety_cfg.get("watchdog_grace_s"),
                          "stale_threshold_s": safety_cfg.get("stale_threshold_s"),
                          "source": "config/safety.json"},
        "dependency_map": dep,
    }


def render(data: dict) -> str:
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", payload)


def build_report(root: Path, run_id: str | None, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(collect(root, run_id)), encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ResiliencePilot Day 1</title>
<style>
:root{--bg:#f7f8fa;--card:#ffffff;--fg:#14171c;--muted:#5b6472;--line:#d9dee6;--accent:#2457c5;
--ok:#1f8a4c;--bad:#c8323b;--warn:#b7791f;--shade:rgba(200,50,59,.09);--grid:#e8ecf2}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--fg:#e7eaef;--muted:#9aa3b2;--line:#2a313b;
--accent:#7aa2ff;--ok:#4cc38a;--bad:#ff6b73;--warn:#e7b562;--shade:rgba(255,107,115,.12);--grid:#232a33}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px 16px 64px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin:18px 0}
h1{font-size:clamp(26px,4.4vw,40px);line-height:1.15;margin:8px 0 10px}
h2{font-size:19px;margin:0 0 12px}
.lede{font-size:17px;color:var(--muted);max-width:760px}
.src{margin-top:14px;padding-top:8px;border-top:1px dashed var(--line);font-size:12px;color:var(--muted);word-break:break-all}
.src code{font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(150px,100%),1fr));gap:12px}
#archSvg,#chart{overflow-x:auto;-webkit-overflow-scrolling:touch}
#archSvg svg,#chart svg{min-width:640px;display:block}
section,p,li,td{overflow-wrap:break-word}
@media (max-width:600px){main{padding:12px 10px 48px}section{padding:14px 12px}.stat .v{font-size:19px}h2{font-size:17px}}
.stat{border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.stat .v{font-size:22px;font-weight:650}
.stat .k{font-size:12px;color:var(--muted)}
.na{color:var(--muted);font-style:italic}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
.tbl{overflow-x:auto}
.tag{display:inline-block;font-size:11px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin:1px;color:var(--muted)}
.badge-ok{color:var(--ok);font-weight:600}.badge-bad{color:var(--bad);font-weight:600}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:10px}
.seg button{background:none;border:0;color:var(--fg);padding:6px 12px;cursor:pointer;font:inherit}
.seg button.on{background:var(--accent);color:#fff}
svg text{fill:var(--fg);font-family:inherit}
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:10px 0}
.controls input[type=range]{flex:1;min-width:180px}
.pbtn{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:8px;padding:5px 12px;cursor:pointer;font:inherit}
.readout{font:12px/1.5 ui-monospace,Consolas,monospace;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;overflow-x:auto;white-space:pre}
.planned{border-style:dashed}
.pill{font-size:11px;font-weight:700;letter-spacing:.04em;color:var(--warn);border:1px solid var(--warn);border-radius:999px;padding:1px 8px;margin-left:8px;vertical-align:middle}
ul{margin:6px 0 0 18px;padding:0}
</style>
</head>
<body>
<main>
<section id="hero">
  <div style="font-size:12px;color:var(--muted);letter-spacing:.06em">RESILIENCEPILOT SDV · DAY 1 · READ-ONLY REPORT</div>
  <h1>HTTP 200 is not the same as healthy data.</h1>
  <p class="lede">The Eclipse SDV Fleet Management rFMS API kept answering HTTP 200, with a fresh-looking
  <code>receivedDateTime</code>, while the vehicle's own <code>createdDateTime</code> stopped advancing. ResiliencePilot detects
  that from the data, injects it on purpose under a safety controller, and verifies recovery.</p>
  <div class="grid" id="heroStats"></div>
  <div class="src" id="srcHero"></div>
</section>

<section id="arch">
  <h2>1 · How it works</h2>
  <div class="seg" role="tablist"><button id="vNormal" class="on">Normal</button><button id="vFault">Fault view</button></div>
  <div id="archSvg"></div>
  <p style="font-size:13px;color:var(--muted);margin:6px 0 0">Arrows show who connects to whom. Labels show edge confidence:
  <b>source</b> = read in the blueprint's code or config · <b>experiment</b> = a fault upstream changed what fms-server returned.
  The telemetry path is highlighted.</p>
  <div class="src">source: <code>config/dependency_map.json</code></div>
</section>

<section id="baseline">
  <h2>2 · Baseline (normal operation)</h2>
  <div class="grid" id="baseStats"></div>
  <h3 style="font-size:15px;margin:16px 0 6px">Recorded load conditions</h3>
  <div id="loadCond"></div>
  <div class="src" id="srcBase"></div>
</section>

<section id="experiment">
  <h2>3 · The experiment: <span id="expTitle"></span></h2>
  <div id="chart"></div>
  <div class="controls">
    <button class="pbtn" id="play">▶ Replay</button>
    <input type="range" id="slider" min="0" value="0" aria-label="replay position">
    <span id="pos" style="font-size:12px;color:var(--muted)"></span>
  </div>
  <div class="readout" id="readout"></div>
  <p style="font-size:12px;color:var(--muted)">Replay steps only through recorded observations (one per poll); nothing is simulated.</p>
  <div class="src" id="srcExp"></div>
</section>

<section id="safety">
  <h2>4 · Safety proof</h2>
  <div class="tbl"><table id="safetyTbl"></table></div>
  <div class="src" id="srcSafety"></div>
</section>

<section id="across">
  <h2>5 · Numbers across runs</h2>
  <div class="grid" id="acrossStats"></div>
  <div class="tbl" style="margin-top:12px"><table id="acrossTbl"></table></div>
  <div class="src" id="srcAcross"></div>
</section>

<section id="limits">
  <h2>6 · Limitations</h2>
  <ul id="limitList"></ul>
  <div class="src" id="srcLimits"></div>
</section>

<section id="next" class="planned">
  <h2>7 · What's next (Day 2)<span class="pill">PLANNED · NOT BUILT</span></h2>
  <ul>
    <li><b>3+ more experiments</b> on the verified path: pause <code>fms-consumer</code>, stop/start <code>fms-forwarder</code>,
      pause the Zenoh router. Each will be repeated with a spread of results reported.</li>
    <li><b>AI planner</b> (advisory only): ranks allowlisted experiments using the dependency map, the baseline and
      earlier outcomes. A human still approves every run, and the safety controller stays in charge.</li>
    <li><b>Findings engine</b>: compares predicted with observed symptoms, links each finding to its evidence, and
      falls back to a deterministic report when the model is unavailable.</li>
  </ul>
  <div class="src">source: <code>docs/ROADMAP.md</code></div>
</section>
<p style="font-size:12px;color:var(--muted);text-align:center" id="gen"></p>
</main>

<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
"use strict";
var D = JSON.parse(document.getElementById("data").textContent);
var NR = '<span class="na">not recorded</span>';
function $(id){return document.getElementById(id);}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
function num(v,d,unit){ if(v===null||v===undefined||v==="") return NR; var n=Number(v); return (isNaN(n)?esc(v):n.toFixed(d))+(unit||""); }
function stat(v,k){ return '<div class="stat"><div class="v">'+v+'</div><div class="k">'+esc(k)+'</div></div>'; }
function src(paths){ return 'source: '+paths.map(function(p){return '<code>'+esc(p)+'</code>';}).join(' · '); }
function svgEl(tag,attrs,text){ var e=document.createElementNS("http://www.w3.org/2000/svg",tag); for(var k in attrs) e.setAttribute(k,attrs[k]); if(text!==undefined) e.textContent=text; return e; }
function css(v){ return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
function range(a){ a=a.filter(function(x){return x!==null&&x!==undefined;}); if(!a.length) return null; return [Math.min.apply(null,a),Math.max.apply(null,a),a.length]; }
function median(a){ a=a.filter(function(x){return x!==null&&x!==undefined;}).sort(function(x,y){return x-y;}); if(!a.length) return null; var m=Math.floor(a.length/2); return a.length%2?a[m]:(a[m-1]+a[m])/2; }

var R = D.run, imp = R.impact, ev = R.events, BL = D.baseline_ref.summary, BF = D.baseline_first.summary;
var thr = (R.limits && R.limits.stale_threshold_s) || D.safety_config.stale_threshold_s;

// ---------- hero ----------
$("heroStats").innerHTML =
  stat(imp.http_ok_ratio_during_fault===null||imp.http_ok_ratio_during_fault===undefined?NR:(imp.http_ok_ratio_during_fault*100).toFixed(0)+"%","HTTP 200 during the fault") +
  stat(num(imp.max_data_age_s,1," s"),"max data age reached") +
  stat(num(imp.time_to_detection_s,2," s"),"time to detect stale data") +
  stat(num(R.recovery.time_to_verified_s,2," s"),"rollback → recovery verified");
$("srcHero").innerHTML = src([R.source]);

// ---------- architecture ----------
var dep = D.dependency_map, faultView = false;
var POS = {"csv-provider":[95,70],"databroker":[355,70],"fms-forwarder":[615,70],"fms-zenoh-router":[875,70],
  "fms-consumer":[875,250],"influxDB":[615,250],"fms-server":[355,250],"grafana":[615,390],"resiliencepilot":[95,250]};
var LABEL = {"csv-provider":"CSV provider","databroker":"KUKSA databroker","fms-forwarder":"fms-forwarder",
  "fms-zenoh-router":"Zenoh router","fms-consumer":"fms-consumer","influxDB":"InfluxDB","fms-server":"fms-server / rFMS API",
  "grafana":"Grafana","resiliencepilot":"ResiliencePilot"};
function drawArch(){
  var W=970,H=440, box=$("archSvg"); box.innerHTML="";
  var s=svgEl("svg",{viewBox:"0 0 "+W+" "+H,width:"100%",role:"img","aria-label":"architecture diagram"});
  var defs=svgEl("defs",{}); var mk=svgEl("marker",{id:"arr",viewBox:"0 0 10 10",refX:"9",refY:"5",markerWidth:"7",markerHeight:"7",orient:"auto-start-reverse"});
  mk.appendChild(svgEl("path",{d:"M0,0 L10,5 L0,10 z",fill:css("--muted")})); defs.appendChild(mk); s.appendChild(defs);
  var target = R.request.target, path = dep.telemetry_path, tIdx = path.indexOf(target);
  var unknownX = 90;
  Object.keys(dep.nodes).forEach(function(n){ if(!POS[n]){ POS[n]=[unknownX,350]; unknownX+=150; }});
  dep.edges.forEach(function(e){
    var a=POS[e.from], b=POS[e.to]; if(!a||!b) return;
    var dx=b[0]-a[0], dy=b[1]-a[1], L=Math.sqrt(dx*dx+dy*dy)||1, ox=dx/L*80, oy=dy/L*28;
    var onPath=e.on_telemetry_path, starved = faultView && onPath && tIdx>=0 &&
      path.indexOf(e.from)>=tIdx && path.indexOf(e.to)>=tIdx && !(e.from==="fms-forwarder"&&e.to==="databroker");
    var color = starved?css("--bad"):(onPath?css("--accent"):css("--muted"));
    s.appendChild(svgEl("line",{x1:a[0]+ox,y1:a[1]+oy,x2:b[0]-ox,y2:b[1]-oy,stroke:color,"stroke-width":onPath?2.4:1.4,
      "stroke-dasharray":starved?"6 5":(onPath?"":"4 4"),"marker-end":"url(#arr)"}));
    var mx=(a[0]+b[0])/2, my=(a[1]+b[1])/2;
    var horiz=Math.abs(dy)<1, vert=Math.abs(dx)<1;
    var t=svgEl("text",{x:mx+(vert?8:0),y:my-(horiz?(e.confidence.length*12+4):0),"font-size":"10.5",
      "text-anchor":vert?"start":"middle",fill:css("--muted"),stroke:css("--card"),"stroke-width":"4","paint-order":"stroke"});
    e.confidence.forEach(function(c,k){ t.appendChild(svgEl("tspan",{x:mx+(vert?8:0),dy:k===0?"0":"12"},c)); });
    s.appendChild(t);
  });
  Object.keys(dep.nodes).forEach(function(n){
    var p=POS[n]; if(!p) return;
    var paused = faultView && n===target;
    var onPath = path.indexOf(n)>=0;
    s.appendChild(svgEl("rect",{x:p[0]-80,y:p[1]-27,width:160,height:54,rx:10,fill:css("--card"),
      stroke:paused?css("--bad"):(onPath?css("--accent"):css("--line")),"stroke-width":paused?3:1.5,"stroke-dasharray":paused?"5 4":""}));
    s.appendChild(svgEl("text",{x:p[0],y:p[1]-2,"text-anchor":"middle","font-size":"13","font-weight":"600"},LABEL[n]||n));
    var sub = paused?"PAUSED (docker pause)":(n===target?"fault target":(dep.nodes[n].side||""));
    s.appendChild(svgEl("text",{x:p[0],y:p[1]+15,"text-anchor":"middle","font-size":"11",fill:paused?css("--bad"):css("--muted")},sub));
  });
  if(faultView){
    s.appendChild(svgEl("text",{x:W-10,y:H-12,"text-anchor":"end","font-size":"12",fill:css("--bad")},
      "Fault view: "+target+" frozen → no new VehicleStatus reaches fms-server, which still answers HTTP 200"));
  }
  box.appendChild(s);
}
$("vNormal").onclick=function(){faultView=false;this.classList.add("on");$("vFault").classList.remove("on");drawArch();};
$("vFault").onclick=function(){faultView=true;this.classList.add("on");$("vNormal").classList.remove("on");drawArch();};

// ---------- baseline ----------
if(BL){
  var age=BL.data_age_s||{}, lat=BL.latency_ms||{}, iv=BL.created_advance_interval_s||{};
  $("baseStats").innerHTML =
    stat(num(lat.median,2," ms"),"rFMS latency median") + stat(num(lat.p95,2," ms"),"rFMS latency p95") +
    stat(BL.http_ok_ratio===null||BL.http_ok_ratio===undefined?NR:(BL.http_ok_ratio*100).toFixed(0)+"%","HTTP success ratio") +
    stat(num(age.median,2," s"),"data age median") + stat(num(age.p95,2," s"),"data age p95") + stat(num(age.max,2," s"),"data age max") +
    stat(num(iv.median,3," s"),"createdDateTime advance interval") + stat(esc(BL.samples)+" @ "+num(BL.interval_s,1," s"),"samples @ poll interval");
  var lc=BL.load_conditions;
  if(lc){
    var rows=[["Vehicles",lc.vehicles],["Forwarder TIMER_INTERVAL",lc.forwarder_timer_interval],["CSV replay (PROVIDER_INFINITE)",lc.csv_provider_infinite],
      ["CSV rows",lc.csv_rows],["Observer poll interval (s)",lc.observer_poll_interval_s],["Other clients",lc.other_clients],["Docker VM memory (GiB)",lc.docker_mem_total_gib]];
    var h='<div class="tbl"><table>'+rows.map(function(r){return '<tr><th style="width:40%">'+esc(r[0])+'</th><td>'+(r[1]===null||r[1]===undefined?NR:esc(r[1]))+'</td></tr>';}).join("")+'</table></div>';
    if(lc.stack_stats&&lc.stack_stats.length){
      h+='<div class="tbl" style="margin-top:8px"><table><tr><th>container (docker stats, end of baseline)</th><th>CPU</th><th>memory</th></tr>'+
        lc.stack_stats.map(function(s){return '<tr><td>'+esc(s.name)+'</td><td>'+esc(s.cpu)+'</td><td>'+esc(s.mem)+'</td></tr>';}).join("")+'</table></div>';
    }
    $("loadCond").innerHTML=h;
  } else { $("loadCond").innerHTML=NR; }
  $("srcBase").innerHTML=src([D.baseline_ref.source]);
} else { $("baseStats").innerHTML=NR; $("loadCond").innerHTML=NR; $("srcBase").innerHTML="source: none found in reports/day1/"; }

// ---------- experiment chart ----------
var O = R.observations, cur = O.length-1;
$("expTitle").textContent = R.request.experiment_type+" · pause "+R.request.target+" for "+R.duration_s+" s (run "+R.run_id+", "+R.state+")";
var slider=$("slider"); slider.max=O.length-1; slider.value=cur;
function drawChart(){
  var W=900,H=330,ml=48,mr=16,mt=16,mb=96, pw=W-ml-mr, ph=H-mt-mb;
  var ts=O.map(function(o){return o.t;}).filter(function(t){return t!==null;});
  var x0=Math.min.apply(null,ts), x1=Math.max.apply(null,ts);
  var ages=O.map(function(o){return o.age;}).filter(function(a){return a!==null;});
  var ymax=Math.ceil(Math.max(Math.max.apply(null,ages),thr)*1.15);
  function X(t){return ml+(t-x0)/(x1-x0||1)*pw;} function Y(a){return mt+ph-(a/ymax)*ph;}
  var s=svgEl("svg",{viewBox:"0 0 "+W+" "+H,width:"100%",role:"img","aria-label":"data age over time"});
  // fault window
  var fEnd = ev.rollback_verified!==null?ev.rollback_verified:ev.fault_window_closed;
  if(ev.injected!==null&&fEnd!==null){
    s.appendChild(svgEl("rect",{x:X(0),y:mt,width:X(fEnd)-X(0),height:ph,fill:css("--shade")}));
    s.appendChild(svgEl("text",{x:(X(0)+X(fEnd))/2,y:mt+ph-8,"text-anchor":"middle","font-size":"11",fill:css("--bad")},"fault window (forwarder paused)"));
  }
  for(var g=0; g<=ymax; g+=Math.max(1,Math.round(ymax/6))){
    s.appendChild(svgEl("line",{x1:ml,x2:ml+pw,y1:Y(g),y2:Y(g),stroke:css("--grid")}));
    s.appendChild(svgEl("text",{x:ml-6,y:Y(g)+4,"text-anchor":"end","font-size":"11",fill:css("--muted")},g+"s"));
  }
  s.appendChild(svgEl("line",{x1:ml,x2:ml+pw,y1:Y(thr),y2:Y(thr),stroke:css("--warn"),"stroke-width":1.6,"stroke-dasharray":"7 5"}));
  s.appendChild(svgEl("text",{x:ml+pw-4,y:Y(thr)-5,"text-anchor":"end","font-size":"11",fill:css("--warn")},"stale threshold "+thr+" s"));
  // line (all faint) + up to cursor (strong)
  function pathUpTo(n){ var d="",first=true; for(var i=0;i<=n;i++){var o=O[i]; if(o.age===null||o.t===null){first=true;continue;} d+=(first?"M":"L")+X(o.t).toFixed(1)+","+Y(o.age).toFixed(1); first=false;} return d; }
  s.appendChild(svgEl("path",{d:pathUpTo(O.length-1),fill:"none",stroke:css("--muted"),"stroke-opacity":".35","stroke-width":1.5}));
  s.appendChild(svgEl("path",{d:pathUpTo(cur),fill:"none",stroke:css("--accent"),"stroke-width":2.4}));
  for(var i=0;i<=cur;i++){ var o=O[i]; if(o.age===null||o.t===null) continue;
    s.appendChild(svgEl("circle",{cx:X(o.t),cy:Y(o.age),r:i===cur?5:2.8,fill:o.freshness==="STALE"?css("--bad"):css("--ok")})); }
  // markers
  var marks=[["fault injected",ev.injected],["stale detected",ev.stale_detected],["rollback verified",ev.rollback_verified],["recovery verified",ev.recovery_verified]];
  marks.forEach(function(m,k){ if(m[1]===null||m[1]===undefined) return;
    s.appendChild(svgEl("line",{x1:X(m[1]),x2:X(m[1]),y1:mt,y2:mt+ph,stroke:css("--fg"),"stroke-opacity":".55","stroke-dasharray":"2 3"}));
    var right=X(m[1])>ml+pw*0.72;
    s.appendChild(svgEl("text",{x:X(m[1])+(right?-4:4),y:mt+14+k*15,"text-anchor":right?"end":"start","font-size":"11",
      stroke:css("--card"),"stroke-width":"4","paint-order":"stroke"},m[0]+" "+m[1].toFixed(2)+"s")); });
  // x axis
  s.appendChild(svgEl("line",{x1:ml,x2:ml+pw,y1:mt+ph,y2:mt+ph,stroke:css("--line")}));
  for(var t=Math.ceil(x0); t<=x1; t+=2){ s.appendChild(svgEl("text",{x:X(t),y:mt+ph+14,"text-anchor":"middle","font-size":"10",fill:css("--muted")},t+"s")); }
  s.appendChild(svgEl("text",{x:ml+pw/2,y:mt+ph+28,"text-anchor":"middle","font-size":"11",fill:css("--muted")},"time relative to verified injection · y = data age at the rFMS API (requestServerDateTime − createdDateTime)"));
  // HTTP strip
  var sy=mt+ph+44, cw=pw/O.length;
  s.appendChild(svgEl("text",{x:ml-6,y:sy+13,"text-anchor":"end","font-size":"10",fill:css("--muted")},"HTTP"));
  O.forEach(function(o,i){ var okk=o.http===200;
    s.appendChild(svgEl("rect",{x:ml+i*cw+0.5,y:sy,width:Math.max(cw-1,1),height:18,rx:2,fill:okk?css("--ok"):css("--bad"),"fill-opacity":i<=cur?"0.9":"0.25"}));
    if(cw>=16) s.appendChild(svgEl("text",{x:ml+i*cw+cw/2,y:sy+13,"text-anchor":"middle","font-size":"9",fill:"#fff"},o.http===null?"ERR":String(o.http))); });
  var okCount=O.filter(function(o){return o.http===200;}).length;
  s.appendChild(svgEl("text",{x:ml,y:sy+36,"font-size":"11"},"HTTP status per poll: "+okCount+" of "+O.length+" polls returned 200, including every poll while the data was stale."));
  $("chart").innerHTML=""; $("chart").appendChild(s);
  var c=O[cur];
  $("pos").textContent="poll "+(cur+1)+" / "+O.length;
  $("readout").textContent=
    "t="+(c.t===null?"n/a":c.t.toFixed(2)+"s")+"  phase="+c.phase+"  HTTP="+(c.http===null?"no response":c.http)+"  freshness="+c.freshness+(c.healthy_but_stale?"  ← HTTP 200 but STALE":"")+"\n"+
    "createdDateTime  (vehicle)  "+(c.created||"not recorded")+"\n"+
    "receivedDateTime (server)   "+(c.received||"not recorded")+"\n"+
    "data age                    "+(c.age===null?"not recorded":c.age.toFixed(3)+" s");
}
slider.oninput=function(){cur=+this.value;drawChart();};
var timer=null;
$("play").onclick=function(){
  if(timer){clearInterval(timer);timer=null;this.textContent="▶ Replay";return;}
  if(cur>=O.length-1) cur=0;
  this.textContent="❚❚ Pause";
  var self=this;
  timer=setInterval(function(){ cur++; slider.value=cur; drawChart(); if(cur>=O.length-1){clearInterval(timer);timer=null;self.textContent="▶ Replay";} },250);
};
$("srcExp").innerHTML=src([R.source])+" · replay speed 2× (recorded poll interval "+num(R.limits.poll_interval_s,1," s")+")";

// ---------- safety ----------
var SC=D.safety_config, es=D.estop_run, la=D.latched_run;
var wdDeadline = Math.min(R.duration_s + (R.limits.watchdog_grace_s||0), SC.max_duration_s||30);
var srows=[
  ["Watchdog cap","Total fault time ≤ "+num(SC.max_duration_s,0," s")+" including grace ("+num(SC.watchdog_grace_s,0," s")+"). For this run the watchdog deadline was "+wdDeadline+" s after injection; the fault window actually closed at "+num(ev.fault_window_closed,2," s")+" (stop reason: "+esc(R.stop_reason)+").",
   "Watchdog firing in a live run: "+NR+" (covered by unit test <code>test_watchdog_rolls_back_when_orchestrator_loop_hangs</code>)"],
  ["Kill switch", es? ("Run <code>"+esc(es.run_id)+"</code> ("+esc(es.duration_s)+" s requested): stop reason <b>"+esc(es.stop_reason)+"</b>, window closed "+num(es.events.fault_window_closed,2," s")+" after injection, rollback verified = <span class='"+(es.rollback.verified?"badge-ok":"badge-bad")+"'>"+es.rollback.verified+"</span> at "+num(es.events.rollback_verified,2," s")+", recovery verified = <span class='"+(es.recovery.verified?"badge-ok":"badge-bad")+"'>"+es.recovery.verified+"</span> (+"+num(es.recovery.time_to_verified_s,2," s")+" after rollback). Final state "+esc(es.state)+".") : NR,
   D.estop_count+" emergency-stop runs recorded"],
  ["Latched unsafe state", la? ("Run <code>"+esc(la.run_id)+"</code>: state <b>"+esc(la.state)+"</b>. Rollback by "+esc(la.rollback.by||"n/a")+" ("+esc(la.rollback.detail||"")+"). The backend was hard-killed mid-fault, and the next start latched the controller until a human reset it.") : NR, "crash test"],
  ["One-experiment lock","Enforced through <code>backend/data/run.lock</code>, which holds the owner's PID across processes. The live second-terminal refusal was seen in the terminal but is not stored in a run record: "+NR+".",
   "unit tests <code>test_H1_*</code>, <code>test_concurrent_request_rejected_while_active</code>"]
];
$("safetyTbl").innerHTML='<tr><th>Control</th><th>Recorded evidence</th><th>Notes</th></tr>'+srows.map(function(r){return '<tr><td><b>'+r[0]+'</b></td><td>'+r[1]+'</td><td>'+r[2]+'</td></tr>';}).join("");
var ssrc=[SC.source,R.source]; if(es) ssrc.push(es.source); if(la) ssrc.push(la.source); ssrc.push("backend/tests/test_safety.py");
$("srcSafety").innerHTML=src(ssrc);

// ---------- across runs ----------
var F=D.full_runs;
function rg(key,sub){ var r=range(F.map(function(x){return (sub?x[sub]:x.impact)[key];})); return r?(r[0].toFixed(2)+" – "+r[1].toFixed(2)+" s"):NR; }
$("acrossStats").innerHTML = stat(F.length,"full-length COMPLETED runs") + stat(rg("time_to_detection_s"),"time to detection (range)") +
  stat(rg("time_to_verified_s","recovery"),"rollback → recovery verified (range)") + stat(rg("time_to_first_fresh_s","recovery"),"rollback → first fresh data (range)");
$("acrossTbl").innerHTML='<tr><th>run</th><th>duration</th><th>HTTP 200</th><th>detect</th><th>max age</th><th>first fresh</th><th>recovery verified</th></tr>'+
  F.map(function(x){return '<tr><td><code>'+esc(x.run_id)+'</code></td><td>'+x.duration_s+' s</td><td>'+(x.impact.http_ok_ratio_during_fault===null?NR:(x.impact.http_ok_ratio_during_fault*100).toFixed(0)+'%')+'</td><td>'+num(x.impact.time_to_detection_s,2," s")+'</td><td>'+num(x.impact.max_data_age_s,2," s")+'</td><td>'+num(x.recovery.time_to_first_fresh_s,2," s")+'</td><td>'+num(x.recovery.time_to_verified_s,2," s")+'</td></tr>';}).join("");
$("srcAcross").innerHTML=src(F.map(function(x){return x.source;}));

// ---------- limitations ----------
var timer_iv = BL&&BL.load_conditions?BL.load_conditions.forwarder_timer_interval:null;
var adv = BL&&BL.created_advance_interval_s?BL.created_advance_interval_s.median:null;
var l1 = BF&&BF.latency_ms?BF.latency_ms.median:null, l2 = BL&&BL.latency_ms?BL.latency_ms.median:null;
$("limitList").innerHTML=[
  "<b>The fault is a container pause, not a network fault.</b> The stack has no proxy, so no network degradation is claimed.",
  "<b>Timestamp cadence is unexplained.</b> The forwarder timer is "+(timer_iv?esc(timer_iv):NR)+", yet <code>createdDateTime</code> advances every "+num(adv,3," s")+" at the API. This has not been investigated.",
  "<b>Latency drift has not been investigated.</b> rFMS latency median was "+num(l1,2," ms")+" in the first baseline and "+num(l2,2," ms")+" in the reference baseline.",
  "<b>Detection time depends on configuration.</b> It is driven by the "+thr+" s stale threshold (<code>config/safety.json</code>), roughly the threshold minus the data age at injection. It is not a property of the system.",
  "Single vehicle, single host, "+F.length+" full-length runs. Not production software and not safety-certified."
].map(function(x){return "<li>"+x+"</li>";}).join("");
$("srcLimits").innerHTML=src([D.baseline_first.source, D.baseline_ref.source, "config/safety.json"]);

$("gen").textContent="Generated "+D.generated_at+" by `rp report` from recorded files only. Static page, so no control here can start, stop or reset an experiment.";
drawArch(); drawChart();
if(window.matchMedia){ window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change",function(){drawArch();drawChart();}); }
})();
</script>
</body>
</html>
"""
