/* OpenLine Wallet – core script (mobile-first) */
/* eslint-disable no-unused-vars */
"use strict";

/* ---------- DOM helpers ---------- */
const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt !== undefined && txt !== null) n.textContent = txt;
  return n;
};

/* ---------- small utils ---------- */
const clamp   = (v, a=0, b=1) => Math.max(a, Math.min(b, Number(v)));
const pct     = (v) => (v*100).toFixed(1) + "%";
const round3  = (v) => (Math.round(Number(v)*1000)/1000).toFixed(3);
const nowISO  = () => new Date().toISOString();
const download = (name, data, type="application/octet-stream") => {
  const blob = new Blob([data], { type });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 0);
};
const clipboard = (text) => navigator.clipboard.writeText(text);

/* ---------- state ---------- */
let RECEIPTS = [];

/* ---------- ingest: demo ---------- */
function makeRandomReceipt(){
  const labs   = ["labA","labB","opsX","unknown"];
  const models = ["demo-llm","op-llm","agent-x"];
  const pick   = (a) => a[Math.floor(Math.random()*a.length)];
  const between = (a,b) => a + Math.random()*(b-a);

  const bucket = Math.random();
  let badge, kRange, dRange, pRange;
  if (bucket < 0.55) { badge="GREEN"; kRange=[0.05,0.50]; dRange=[0.00,0.28]; pRange=[0.75,0.98]; }
  else if (bucket < 0.90) { badge="AMBER"; kRange=[0.50,0.90]; dRange=[0.28,0.60]; pRange=[0.45,0.80]; }
  else { badge="RED"; kRange=[0.90,1.20]; dRange=[0.60,1.00]; pRange=[0.10,0.45]; }

  return {
    rid: crypto.randomUUID(),
    issuer_id: pick(labs),
    model: pick(models),
    issued_at: nowISO(),
    attrs: { status: badge },
    signals: {
      kappa    : +between(...kRange).toFixed(3),
      dhol     : +between(...dRange).toFixed(3),
      phi_star : +between(...pRange).toFixed(3)
    },
    bytes: 512 + Math.floor(Math.random()*120)
  };
}

/* ---------- ingest: normalize & add ---------- */
function normalizeIncoming(obj, rawText=""){
  // bytes
  try { obj.bytes = new TextEncoder().encode(rawText).length; } catch { obj.bytes ??= (rawText||"").length; }
  // ids/status
  obj.rid       = obj.rid || crypto.randomUUID();
  obj.issuer_id = obj.issuer_id || "unknown";
  if (!obj.attrs) obj.attrs = {};
  if (obj.status && !obj.attrs.status) obj.attrs.status = String(obj.status).toUpperCase();
  if (!obj.attrs.status) obj.attrs.status = "GREEN"; // sensible default
  // signals mapping (support short keys)
  obj.signals = obj.signals || {};
  if (obj.kappa != null && obj.signals.kappa == null) obj.signals.kappa = Number(obj.kappa);
  if (obj.dhol  != null && obj.signals.dhol  == null) obj.signals.dhol  = Number(obj.dhol);
  const phi = obj.phi_star ?? obj.phi;
  if (phi != null && obj.signals.phi_star == null) obj.signals.phi_star = Number(phi);
  // optional extras used by heuristics
  if (!obj.guards && typeof obj.ucr === "number") obj.guards = { ucr: obj.ucr };
  return obj;
}

function addReceipt(r){ RECEIPTS.push(r); }
function addRandom(n=1){ for (let i=0;i<n;i++) addReceipt(makeRandomReceipt()); renderAll(); }

/* ---------- ingest: URL / GitHub raw ---------- */
function toRaw(urlString){
  try{
    const url = new URL(urlString);
    if (url.hostname === "github.com"){
      const p = url.pathname.split("/").filter(Boolean);
      if (p[2] === "blob"){
        return `https://raw.githubusercontent.com/${p[0]}/${p[1]}/${p[3]}/${p.slice(4).join("/")}`;
      }
    }
    return url.toString();
  } catch { return urlString; }
}

async function addByUrl(u){
  try{
    const raw = toRaw(u.trim());
    const res = await fetch(raw + (raw.includes("?") ? "&" : "?") + "v=" + Date.now(), { cache: "no-store" });
    const txt = await res.text();
    const obj = JSON.parse(txt);
    normalizeIncoming(obj, txt);
    addReceipt(obj);
    renderAll();
  } catch (e){
    alert("Add failed. Expecting a single JSON receipt at the URL.");
    console.error(e);
  }
}

/* ---------- ingest: files & paste (JSON or JSONL) ---------- */
async function addFromFile(file){
  try{
    const txt = await file.text();
    addFromText(txt);
  }catch(e){ alert("Import failed."); console.error(e); }
}

function parseLinesOrJson(text){
  const t = text.trim();
  if (!t) return [];
  // JSONL (newline-delimited JSON objects)
  const looksLikeJsonl = t.split("\n").length > 1 && !t.startsWith("[") && !t.endsWith("]");
  if (looksLikeJsonl){
    return t.split("\n").map(s => s.trim()).filter(Boolean).map(JSON.parse);
  }
  // Single JSON object or array
  const obj = JSON.parse(t);
  return Array.isArray(obj) ? obj : [obj];
}

function addFromText(text){
  try{
    const arr = parseLinesOrJson(text);
    arr.forEach(o => { normalizeIncoming(o, JSON.stringify(o)); addReceipt(o); });
    renderAll();
  }catch(e){
    alert("Paste/import failed. Provide JSON (object/array) or JSONL.");
    console.error(e);
  }
}

/* ---------- heuristics (UI-only) ---------- */
/* VKD proxy: simple safety/viability margin from signals */
function vkdProxy(signals={}){
  const k = clamp(signals.kappa ?? 0.5);
  const d = clamp(signals.dhol  ?? 0.5);
  const p = clamp(signals.phi_star ?? 0.5);
  // Higher κ and Δhol hurt; higher Φ* helps.
  const margin = clamp(0.5*(1 - p) + 0.3*k + 0.2*d, 0, 1);
  return 1 - margin; // 1 = comfy, 0 = at risk
}

/* Observability window: can ops "see" enough to trust deploys? */
function obsProxy(signals={}, guards={}){
  const k  = clamp(signals.kappa ?? 0.5);
  const d  = clamp(signals.dhol  ?? 0.5);
  const u  = clamp(guards.ucr    ?? 0.5); // unsupported-claim ratio if present
  const es = clamp(signals.evidence_strength ?? 0.5);
  // capacity: how much you can reliably observe; load: how spicy the run is
  const capacity = (1 - k) * (1 - d) * es;
  const load     = 0.5*u + 0.5*d;
  const r = capacity / (capacity + load + 1e-9);
  return clamp(r);
}

/* ---------- analytics ---------- */
function avg(a){ return a.length ? a.reduce((x,y)=>x+y,0)/a.length : 0; }

function percentile(arr, q){
  if (!arr.length) return 0;
  const a = [...arr].sort((x,y)=>x-y);
  const i = clamp((a.length - 1) * q, 0, a.length - 1);
  const lo = Math.floor(i), hi = Math.ceil(i);
  return lo === hi ? a[lo] : a[lo] + (a[hi]-a[lo])*(i-lo);
}

function slopeLast(arr){
  if (arr.length < 4) return 0;
  const n   = Math.min(arr.length, 12);
  const xs  = Array.from({length:n}, (_,i)=>i);
  const ys  = arr.slice(-n);
  const x̄   = xs.reduce((a,b)=>a+b,0)/n;
  const ȳ   = ys.reduce((a,b)=>a+b,0)/n;
  let num=0, den=0;
  for (let i=0;i<n;i++){ num += (xs[i]-x̄)*(ys[i]-ȳ); den += (xs[i]-x̄)**2; }
  return den ? num/den : 0; // positive = rising drift
}

function scoreHealth(green, k95, dTrend, phiFloor){
  const g = clamp(green);
  const k = clamp(k95);
  const d = clamp(dTrend);
  const p = clamp(phiFloor);
  return Math.round(100 * (0.5*g + 0.2*(1-k) + 0.2*(1-d) + 0.1*p));
}

function adviceFor(s){
  const msgs = [];
  if (s.k95 >= 0.95)  msgs.push("Brake: κ high → reduce chain width/sampling; roll back risky config.");
  if (s.phiFloor < 0.20) msgs.push("Raise Φ* floor: add test prompts or clamp temperature.");
  if (s.dholTrend >= 0.15) msgs.push("Drift ↑: diff configs/data since last stable run.");
  if (s.greenRate < 0.60) msgs.push("Green rate low: simplify tool/step graph.");
  if (s.obsAvg < 0.40) msgs.push("Obs window tight: receipts mandatory for deploys.");
  if (!msgs.length) msgs.push("Stable: hold config; weekly sample controls.");
  return msgs.join(" ");
}

function computeIssuerStats(items){
  const byIssuer = {};
  for (const r of items){
    const id = r.issuer_id || "unknown";
    const k  = r.signals?.kappa;
    const d  = r.signals?.dhol;
    const p  = r.signals?.phi_star;
    const badge = (r.attrs?.status || "").toUpperCase();

    (byIssuer[id] ||= {
      id, total:0, green:0,
      kappas:[], dhols:[], phis:[], last:[],
      vkdList:[], obsList:[]
    });

    const b = byIssuer[id];
    b.total++; if (badge === "GREEN") b.green++;
    if (Number.isFinite(k)) b.kappas.push(k);
    if (Number.isFinite(d)) b.dhols.push(d);
    if (Number.isFinite(p)) b.phis.push(p);
    b.vkdList.push(vkdProxy(r.signals));
    b.obsList.push(obsProxy(r.signals, r.guards || {}));
    b.last.push({ t: Date.parse(r.issued_at || nowISO()), d });
  }

  const out = [];
  for (const id in byIssuer){
    const x = byIssuer[id];
    const s = {
      id,
      total: x.total,
      greenRate : x.total ? x.green/x.total : 0,
      k95       : percentile(x.kappas, 0.95),
      phiFloor  : x.phis.length ? Math.min(...x.phis) : 0,
      dholTrend : slopeLast(x.last.map(o=>o.d ?? 0)),
      vkdAvg    : avg(x.vkdList),
      obsAvg    : avg(x.obsList)
    };
    s.health = scoreHealth(s.greenRate, s.k95, s.dholTrend, s.phiFloor);
    s.advice = adviceFor(s);
    out.push(s);
  }
  out.sort((a,b)=>b.health - a.health);
  return out;
}

function overallFrom(stats){
  if (!stats.length) return { score:0, txt:"—", worstAdvice:"—" };
  const w    = (s) => s.total;
  const sumW = stats.reduce((a,b)=>a+w(b),0);
  const score = Math.round(stats.reduce((a,s)=>a + s.health*w(s),0)/sumW);
  const total = stats.reduce((a,s)=>a+s.total,0);
  const greens = stats.reduce((a,s)=>a+s.greenRate*s.total,0);
  const greenRate = total ? greens/total : 0;
  const txt = `${(greenRate*100).toFixed(1)}% green • ${total} receipts`;
  const worst = [...stats].sort((a,b)=>a.health - b.health)[0];
  return { score, txt, worstAdvice: worst ? worst.advice : "—" };
}

/* ---------- rendering ---------- */
function metric(key,val){
  const m = el("div","metric");
  m.appendChild(el("div","mkey", key));
  m.appendChild(el("div","mval", val));
  return m;
}
function chip(txt, cls=""){ return el("span", "pill " + cls, txt); }

function renderIssuerCards(stats){
  const box = $("#issuerCards"); box.innerHTML = "";
  stats.forEach(s => {
    const card = el("div","rcpt issuer-card");

    const head = el("div","center");
    head.appendChild(el("div","issuer", s.id));
    head.appendChild(el("div","knum", String(s.health)));
    card.appendChild(head);

    const line = el("div","right");
    line.appendChild(chip(`Obs ${(s.obsAvg*100|0)}%`));
    line.appendChild(chip(`VKD ${(s.vkdAvg*100|0)}%`));
    card.appendChild(line);

    const grid = el("div","grid");
    grid.appendChild(metric("Green", pct(s.greenRate)));
    grid.appendChild(metric("κ p95", round3(s.k95)));
    grid.appendChild(metric("Δhol trend", (s.dholTrend>=0?"+":"") + round3(s.dholTrend)));
    grid.appendChild(metric("Φ* floor", round3(s.phiFloor)));
    card.appendChild(grid);

    const advice = el("div","note tight", s.advice);
    card.appendChild(advice);

    card.addEventListener("click", () => card.classList.toggle("compact"));
    box.appendChild(card);
  });
}

function renderReceipts(list){
  const box = $("#receiptList"); box.innerHTML = "";
  const recent = [...list].slice(-25).reverse();

  recent.forEach(r => {
    const tile = el("div","rcpt " + (
      (r.attrs?.status === "RED") ? "danger" : (r.attrs?.status === "GREEN" ? "okay" : "")
    ));

    const h = el("div","h");
    const when = (r.issued_at || "—").replace("T"," ").replace("Z","");
    h.appendChild(el("div","ell", when));

    const pills = el("div","right");
    pills.appendChild(chip(r.issuer_id || "unknown"));
    pills.appendChild(chip(r.model || "—"));
    const badge = (r.attrs?.status || "—").toUpperCase();
    pills.appendChild(chip(badge, badge==="GREEN"?"ok":badge==="AMBER"?"warn":"bad"));

    const v = vkdProxy(r.signals), o = obsProxy(r.signals, r.guards || {});
    pills.appendChild(chip(`Obs ${String((o*100)|0)}%`));
    pills.appendChild(chip(`VKD ${String((v*100)|0)}%`));
    h.appendChild(pills);
    tile.appendChild(h);

    const sub = el("div","subline");
    sub.appendChild(chip("κ "   + round3(r.signals?.kappa ?? 0)));
    sub.appendChild(chip("Δhol "+ round3(r.signals?.dhol  ?? 0)));
    sub.appendChild(chip("Φ* "  + round3(r.signals?.phi_star ?? 0)));
    sub.appendChild(chip(String(r.bytes || 0) + "B"));
    tile.appendChild(sub);

    box.appendChild(tile);
  });

  $("#receiptCount").textContent =
    `${list.length} total • showing ${Math.min(25, list.length)}`;
}

function renderDeepTable(list){
  const wrap = $("#deepTable");
  wrap.innerHTML = "";
  const tbl = document.createElement("table");
  tbl.style.width = "660px";
  tbl.style.borderCollapse = "collapse";
  tbl.style.fontSize = "13px";

  const thead = document.createElement("thead");
  thead.innerHTML = `<tr>
    <th style="text-align:left;padding:8px;border-bottom:1px solid var(--line)">When</th>
    <th style="text-align:left;padding:8px;border-bottom:1px solid var(--line)">Issuer</th>
    <th style="text-align:left;padding:8px;border-bottom:1px solid var(--line)">Model</th>
    <th style="text-align:left;padding:8px;border-bottom:1px solid var(--line)">Badge</th>
    <th style="text-align:right;padding:8px;border-bottom:1px solid var(--line)">κ</th>
    <th style="text-align:right;padding:8px;border-bottom:1px solid var(--line)">Δhol</th>
    <th style="text-align:right;padding:8px;border-bottom:1px solid var(--line)">Φ*</th>
    <th style="text-align:right;padding:8px;border-bottom:1px solid var(--line)">bytes</th>
  </tr>`;
  tbl.appendChild(thead);

  const tb = document.createElement("tbody");
  list.slice(-200).reverse().forEach(r=>{
    const tr = document.createElement("tr");
    const td = (t,align="left") => {
      const d = document.createElement("td");
      d.style.padding="8px"; d.style.borderBottom="1px solid var(--line)";
      d.style.textAlign=align; d.textContent=t; return d;
    };
    tr.appendChild(td((r.issued_at||"—").replace("T"," ").replace("Z","")));
    tr.appendChild(td(r.issuer_id||"unknown"));
    tr.appendChild(td(r.model||"—"));
    tr.appendChild(td((r.attrs?.status||"—").toUpperCase()));
    tr.appendChild(td(round3(r.signals?.kappa ?? 0), "right"));
    tr.appendChild(td(round3(r.signals?.dhol  ?? 0), "right"));
    tr.appendChild(td(round3(r.signals?.phi_star ?? 0), "right"));
    tr.appendChild(td(String(r.bytes || 0), "right"));
    tb.appendChild(tr);
  });
  tbl.appendChild(tb);
  wrap.appendChild(tbl);
}

/* ---------- top render ---------- */
function renderAll(){
  const stats = computeIssuerStats(RECEIPTS);
  const ov    = overallFrom(stats);
  $("#postureLine").textContent = ov.txt;
  $("#overallScore").textContent = String(ov.score);
  $("#adviceLine").textContent = ov.worstAdvice;
  renderIssuerCards(stats);
  renderReceipts(RECEIPTS);
}

/* ---------- export ---------- */
function asJSONL(arr){ return arr.map(o => JSON.stringify(o)).join("\n"); }

async function exportPDF(){
  try{
    if (!window.PDFLib){
      await new Promise((res,rej)=>{
        const s = document.createElement("script");
        s.src   = "https://cdn.jsdelivr.net/npm/pdf-lib@1.17.1/dist/pdf-lib.min.js";
        s.onload = res; s.onerror = rej; document.head.appendChild(s);
      });
    }
    const { PDFDocument, StandardFonts } = window.PDFLib;
    const pdf  = await PDFDocument.create();
    const page = pdf.addPage([612, 792]);
    const font = await pdf.embedFont(StandardFonts.Helvetica);
    const bold = await pdf.embedFont(StandardFonts.HelveticaBold);

    const stats = computeIssuerStats(RECEIPTS);
    const ov    = overallFrom(stats);

    let y = 760;
    page.drawText("OpenLine Wallet — Compliance Summary", { x:40,y, size:16, font:bold }); y -= 22;
    page.drawText(new Date().toISOString(), { x:40,y, size:10, font }); y -= 24;
    page.drawText(`Overall health: ${ov.score}`, { x:40,y, size:14, font:bold }); y -= 18;
    page.drawText(`Posture: ${ov.txt}`, { x:40,y, size:12, font }); y -= 16;
    page.drawText(`Advice: ${ov.worstAdvice}`, { x:40,y, size:12, font }); y -= 24;

    page.drawText("Issuer health (7 days)", { x:40,y, size:12, font:bold }); y -= 16;
    stats.slice(0,10).forEach(s=>{
      const line = `${s.id.padEnd(10)}  Health ${String(s.health).padStart(3)}  `
        + `Green ${(s.greenRate*100).toFixed(1)}%  κ95 ${round3(s.k95)}  `
        + `ΔholTrend ${(s.dholTrend>=0?"+":"") + round3(s.dholTrend)}  `
        + `Φ*floor ${round3(s.phiFloor)}  Obs ${(s.obsAvg*100|0)}%  VKD ${(s.vkdAvg*100|0)}%`;
      page.drawText(line, { x:40,y, size:10, font }); y -= 14;
    });

    y -= 10;
    page.drawText("Top 20 receipts (most recent)", { x:40,y, size:12, font:bold }); y -= 16;
    RECEIPTS.slice(-20).reverse().forEach(r=>{
      const line = `${(r.issued_at||"—").replace("T"," ").replace("Z","")}  `
        + `${r.issuer_id||"unk"}  ${r.model||"—"}  ${(r.attrs?.status||"—").toUpperCase()}  `
        + `κ ${round3(r.signals?.kappa||0)}  Δ ${round3(r.signals?.dhol||0)}  `
        + `Φ* ${round3(r.signals?.phi_star||0)}  `
        + `Obs ${(obsProxy(r.signals, r.guards||{})*100|0)}%  VKD ${(vkdProxy(r.signals)*100|0)}%`;
      page.drawText(line, { x:40,y, size:9, font }); y -= 12;
    });

    const bytes = await pdf.save();
    download(`openline-wallet-${Date.now()}.pdf`, bytes, "application/pdf");
  }catch(e){ alert("PDF export failed."); console.error(e); }
}

/* ---------- events ---------- */
function wireEvents(){
  $("#addRand")   .addEventListener("click", () => addRandom(1));
  $("#addRand5")  .addEventListener("click", () => addRandom(5));
  $("#exportJsonl").addEventListener("click", () => download(`receipts-${Date.now()}.jsonl`, asJSONL(RECEIPTS), "application/jsonl"));

  $("#addUrl").addEventListener("click", () => {
    const u = $("#urlBox").value.trim(); if (!u) return;
    addByUrl(u); $("#urlBox").value = "";
  });

  $("#addFile").addEventListener("click", () => {
    const f = $("#fileBox").files?.[0]; if (!f) return;
    addFromFile(f);
  });

  $("#addPaste").addEventListener("click", () => {
    const t = $("#pasteBox").value.trim(); if (!t) return;
    addFromText(t); $("#pasteBox").value = "";
  });

  $("#clearAll").addEventListener("click", () => {
    if (!confirm("Clear all receipts?")) return;
    RECEIPTS = []; renderAll();
  });

  $("#copySummary").addEventListener("click", () => {
    const stats = computeIssuerStats(RECEIPTS);
    const ov    = overallFrom(stats);
    const lines = [
      "OpenLine Wallet — 7-day summary",
      `Overall health: ${ov.score} (${ov.txt})`,
      `Advice: ${ov.worstAdvice}`,
      "Issuers:",
      ...stats.map(s => `  ${s.id}: health ${s.health}, green ${pct(s.greenRate)}, κ95 ${round3(s.k95)}, ΔholTrend ${(s.dholTrend>=0?"+":"") + round3(s.dholTrend)}, Φ*floor ${round3(s.phiFloor)}, Obs ${(s.obsAvg*100|0)}%, VKD ${(s.vkdAvg*100|0)}%`)
    ];
    clipboard(lines.join("\n")).then(()=>alert("Summary copied."));
  });

  $("#exportPdf").addEventListener("click", exportPDF);

  $("#toggleDeep").addEventListener("click", () => {
    const w = $("#deepTableWrap");
    const show = w.style.display === "none";
    w.style.display = show ? "block" : "none";
    if (show) renderDeepTable(RECEIPTS);
  });

  // drag & drop anywhere
  document.addEventListener("dragover", (e)=>{ e.preventDefault(); });
  document.addEventListener("drop", async (e)=>{
    e.preventDefault();
    const f = e.dataTransfer?.files?.[0]; if (!f) return;
    addFromFile(f);
  });
}

/* ---------- boot ---------- */
(function boot(){
  wireEvents();
  const q = new URLSearchParams(location.search);
  const demo = q.get("demo");
  const u    = q.get("u");
  if (u) addByUrl(u);
  if (demo !== "0") addRandom(12);
  $("#windowText").textContent = "7-day posture";
})();
