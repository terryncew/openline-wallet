/* OpenLine Wallet — hardened runtime (paste/file/URL ingest + VKD/Obs) */
/* MIT. No deps. Safe to drop into GH Pages. */

(function(){
  "use strict";

  /* ---------- DOM helpers ---------- */
  const $ = sel => document.querySelector(sel);
  const el = (t,c,txt)=>{ const e=document.createElement(t); if(c) e.className=c; if(txt!=null) e.textContent=txt; return e; };

  /* ---------- math helpers ---------- */
  const clamp01 = v => Math.max(0, Math.min(1, Number(v)));
  const pct = v => (v*100).toFixed(1)+'%';
  const round3 = v => (Math.round(Number(v||0)*1000)/1000).toFixed(3);
  const nowISO = ()=> new Date().toISOString();

  /* ---------- demo generator ---------- */
  function makeRandomReceipt(){
    const labs = ['labA','labB','opsX','unknown'];
    const models = ['demo-llm','op-llm','agent-x'];
    const bucket = Math.random();
    let badge,kR,dR,pR;
    if (bucket < 0.55){ badge='GREEN'; kR=[0.05,0.50]; dR=[0.00,0.28]; pR=[0.75,0.98]; }
    else if (bucket < 0.90){ badge='AMBER'; kR=[0.50,0.90]; dR=[0.28,0.60]; pR=[0.45,0.80]; }
    else { badge='RED'; kR=[0.90,1.20]; dR=[0.60,1.00]; pR=[0.10,0.45]; }
    const pick = a => a[Math.floor(Math.random()*a.length)];
    const between = (a,b)=> a + Math.random()*(b-a);
    return {
      rid: crypto.randomUUID(),
      issuer_id: pick(labs),
      model: pick(models),
      issued_at: nowISO(),
      attrs:{ status: badge },
      signals:{
        kappa:+between(...kR).toFixed(3),
        dhol:+between(...dR).toFixed(3),
        phi_star:+between(...pR).toFixed(3),
        // default evidence strength keeps demo realistic
        evidence_strength:+between(0.60,0.90).toFixed(3)
      },
      guards:{ ucr:+between(0.10,0.30).toFixed(3) },
      bytes: 512 + Math.floor(Math.random()*120)
    };
  }

  /* ---------- store ---------- */
  let RECEIPTS = [];
  const addReceipt = r => { RECEIPTS.push(r); };

  function addRandom(n=1){ for (let i=0;i<n;i++) addReceipt(makeRandomReceipt()); renderAll(); }

  /* ---------- tolerant ingest (URL / File / Paste) ---------- */
  function toRaw(u){
    try{
      const url = new URL(u);
      if (url.hostname === 'github.com'){
        const p = url.pathname.split('/').filter(Boolean);
        if (p[2] === 'blob'){
          return `https://raw.githubusercontent.com/${p[0]}/${p[1]}/${p[3]}/${p.slice(4).join('/')}`;
        }
      }
      return u;
    }catch{ return u; }
  }

  async function addByUrl(u){
    try{
      const raw = toRaw(u.trim());
      const res = await fetch(raw + (raw.includes('?')?'&':'?') + 'v=' + Date.now(), { cache:'no-store' });
      const txt = await res.text();
      // Can be JSON, array, or JSONL
      const objs = parseLoose(txt);
      for (const obj of objs){ normalizeIncoming(obj, JSON.stringify(obj)); addReceipt(obj); }
      renderAll();
    }catch(e){
      alert('Add failed: ' + (e?.message || 'network/JSON error'));
    }
  }

  async function addFromFile(f){
    const txt = await f.text();
    addFromText(txt);
  }

  function addFromText(txt){
    try{
      const objs = parseLoose(txt);
      for (const obj of objs){ normalizeIncoming(obj, JSON.stringify(obj)); addReceipt(obj); }
      renderAll();
    }catch(e){
      alert('Paste/import failed: ' + e.message + '\nProvide JSON object/array or JSONL.');
    }
  }

  /* tolerant JSON/JSONL parser (accepts comments & trailing commas) */
  function stripComments(s){ return s.replace(/\/\*[\s\S]*?\*\//g,'').replace(/\/\/.*$/gm,''); }
  function stripTrailingCommas(s){ return s.replace(/,\s*([}\]])/g,'$1'); }

  function parseLoose(text){
    let raw = (text||'').trim();
    if (!raw) throw new Error('Empty input');

    const tryWhole = (s)=>{
      const cleaned = stripTrailingCommas(stripComments(s));
      try{
        const v = JSON.parse(cleaned);
        return Array.isArray(v) ? v : [v];
      }catch{ return null; }
    };

    // try whole-doc JSON first
    let out = tryWhole(raw);
    if (out) return out;

    // then JSONL
    const lines = raw.split(/\r?\n/).map(l=>l.trim()).filter(Boolean);
    if (lines.length > 1){
      const arr = [];
      for (let i=0;i<lines.length;i++){
        const cleaned = stripTrailingCommas(stripComments(lines[i]));
        try{ arr.push(JSON.parse(cleaned)); }
        catch(e){ throw new Error(`Line ${i+1}: ${e.message}`); }
      }
      return arr;
    }

    throw new Error('Unrecognized format');
  }

  /* normalize inbound to wallet's expected shape */
  function normalizeIncoming(json, txt){
    json.bytes = new TextEncoder().encode(txt).length;
    json.rid = json.rid || crypto.randomUUID();
    json.issuer_id = json.issuer_id || 'unknown';
    json.attrs = json.attrs || { status: (json.status||'').toUpperCase() };
    json.signals = json.signals || {
      kappa: json.kappa,
      dhol: json.dhol,
      phi_star: json.phi || json.phi_star
    };
    json.signals.evidence_strength = json.signals.evidence_strength ?? json.evidence_strength ?? 0.5;
    json.guards = json.guards || {};
    json.guards.ucr = json.guards.ucr ?? json.ucr ?? 0.5;
  }

  /* ---------- analytics: VKD + Obs Window ---------- */

  // Canonical VKD (viability–decoherence margin). Tunable weights.
  const VKD_W = { a:0.5, b:0.3, c:0.2 }; // a→(1-Φ*), b→κ, c→Δhol
  function vkdProxy(signals={}){
    const p = clamp01(signals.phi_star ?? signals.phi ?? 0.5);
    const k = clamp01(signals.kappa ?? 0.5);
    const d = clamp01(signals.dhol  ?? 0.5);
    const risk = VKD_W.a*(1 - p) + VKD_W.b*k + VKD_W.c*d;
    return clamp01(1 - risk);
  }

  // Observability window: capacity vs. load
  function obsProxy(signals={}, guards={}){
    const k  = clamp01(signals.kappa ?? 0.5);
    const d  = clamp01(signals.dhol  ?? 0.5);
    const es = clamp01(signals.evidence_strength ?? 0.5);
    const u  = clamp01(guards.ucr ?? 0.5);
    const capacity = (1 - k) * (1 - d) * es;
    const load = 0.5*u + 0.5*d;
    const r = capacity / (capacity + load + 1e-9);
    return clamp01(r);
  }

  function percentile(arr,q){
    if (!arr.length) return 0;
    const a=[...arr].sort((x,y)=>x-y);
    const i=Math.max(0,Math.min(a.length-1,(a.length-1)*q));
    const lo=Math.floor(i), hi=Math.ceil(i);
    return lo===hi? a[lo] : a[lo]+(a[hi]-a[lo])*(i-lo);
  }

  function slopeLast(arr){
    if (arr.length<4) return 0;
    const n = Math.min(arr.length,12);
    const xs = Array.from({length:n},(_,i)=>i);
    const ys = arr.slice(-n);
    const xbar = xs.reduce((a,b)=>a+b,0)/n;
    const ybar = ys.reduce((a,b)=>a+b,0)/n;
    let num=0, den=0;
    for (let i=0;i<n;i++){ num += (xs[i]-xbar)*(ys[i]-ybar); den += (xs[i]-xbar)**2; }
    return den ? num/den : 0;
  }

  function scoreHealth(green,k95,dTrend,phiFloor){
    const g = clamp01(green), k=clamp01(k95), d=clamp01(dTrend), p=clamp01(phiFloor);
    return Math.round(100*(0.5*g + 0.2*(1-k) + 0.2*(1-d) + 0.1*p));
  }

  function computeIssuerStats(items){
    const by = {};
    for (const r of items){
      const id = r.issuer_id || 'unknown';
      const k=r.signals?.kappa, d=r.signals?.dhol, p=r.signals?.phi_star;
      const badge=(r.attrs?.status||'').toUpperCase();
      (by[id] ||= {id, total:0, green:0, kappas:[], dhols:[], phis:[], last:[], vkd:[], obs:[]});
      const b = by[id];
      b.total++; if (badge==='GREEN') b.green++;
      if (Number.isFinite(k)) b.kappas.push(k);
      if (Number.isFinite(d)) b.dhols.push(d);
      if (Number.isFinite(p)) b.phis.push(p);
      b.vkd.push(vkdProxy(r.signals));
      b.obs.push(obsProxy(r.signals, r.guards||{}));
      b.last.push({t:Date.parse(r.issued_at||nowISO()), d});
    }
    const out=[];
    for (const id in by){
      const s = by[id];
      const greenRate = s.total ? s.green/s.total : 0;
      const k95 = percentile(s.kappas,0.95);
      const phiFloor = s.phis.length? Math.min(...s.phis) : 0;
      const dholTrend = slopeLast(s.last.map(o=>o.d ?? 0));
      const vkdAvg = s.vkd.length? s.vkd.reduce((a,b)=>a+b,0)/s.vkd.length : 0;
      const obsAvg = s.obs.length? s.obs.reduce((a,b)=>a+b,0)/s.obs.length : 0;
      const health = scoreHealth(greenRate,k95,dholTrend,phiFloor);
      out.push({ id, greenRate, k95, phiFloor, dholTrend, vkdAvg, obsAvg, health, total:s.total, green:s.green });
    }
    out.sort((a,b)=>b.health-a.health);
    return out;
  }

  function overallFrom(stats){
    if (!stats.length) return {score:0, txt:'—', worstAdvice:'—'};
    const w = s => s.total;
    const sumW = stats.reduce((a,b)=>a+w(b),0);
    const score = Math.round(stats.reduce((a,s)=>a + s.health*w(s),0)/sumW);
    const total = stats.reduce((a,s)=>a+s.total,0);
    const green = (stats.reduce((a,s)=>a+s.green,0) / total) || 0;
    const txt = `${(green*100).toFixed(1)}% green • ${total} receipts`;
    const worst = [...stats].sort((a,b)=>a.health-b.health)[0];
    return { score, txt, worstAdvice: adviceFor(worst) };
  }

  function adviceFor(s){
    const msgs=[];
    if (!s) return '—';
    if (s.k95>=0.95) msgs.push('Brake: κ high → reduce chain width / sampling; roll back risky config.');
    if (s.phiFloor<0.20) msgs.push('Raise Φ* floor: add test prompts or clamp temperature.');
    if (s.dholTrend>=0.15) msgs.push('Drift ↑: diff configs/data since last stable run.');
    if (s.greenRate<0.60) msgs.push('Green rate low: simplify tool/step graph.');
    if (s.obsAvg<0.40) msgs.push('Obs window tight: receipts mandatory for deploys.');
    if (!msgs.length) msgs.push('Stable: hold config; weekly sample controls.');
    return msgs.join(' ');
  }

  /* ---------- rendering ---------- */
  function metric(k,v){
    const m = el('div','metric'); m.appendChild(el('div','mkey',k)); m.appendChild(el('div','mval',v)); return m;
  }
  function chip(txt,cls=''){ const s=el('span','pill '+cls,txt); return s; }

  function renderIssuerCards(stats){
    const box = $('#issuerCards'); if (!box) return;
    box.innerHTML = '';
    stats.forEach(s=>{
      const card = el('div','rcpt issuer-card');
      const head = el('div','center');
      head.appendChild(el('div','issuer', s.id));
      head.appendChild(el('div','knum', String(s.health)));
      card.appendChild(head);

      const line = el('div','right');
      line.appendChild(chip(`Obs ${(s.obsAvg*100|0)}%`));
      line.appendChild(chip(`VKD ${(s.vkdAvg*100|0)}%`));
      card.appendChild(line);

      const grid = el('div','grid');
      grid.appendChild(metric('Green', pct(s.greenRate)));
      grid.appendChild(metric('κ p95', round3(s.k95)));
      grid.appendChild(metric('Δhol trend', (s.dholTrend>=0?'+':'')+round3(s.dholTrend)));
      grid.appendChild(metric('Φ* floor', round3(s.phiFloor)));
      card.appendChild(grid);

      const advice = el('div','note tight', adviceFor(s));
      card.appendChild(advice);

      card.addEventListener('click', ()=> card.classList.toggle('compact'));
      box.appendChild(card);
    });
  }

  function renderReceipts(list){
    const box = $('#receiptList'); if (!box) return;
    box.innerHTML='';
    const recent = [...list].slice(-25).reverse();
    recent.forEach(r=>{
      const tile = el('div','rcpt ' + (r.attrs?.status==='RED'?'danger':r.attrs?.status==='GREEN'?'okay':''));
      const h = el('div','h');
      h.appendChild(el('div','ell', (r.issued_at||'—').replace('T',' ').replace('Z','')));
      const pills = el('div','right');
      pills.appendChild(chip(r.issuer_id));
      pills.appendChild(chip(r.model));
      const badge = (r.attrs?.status||'—').toUpperCase();
      pills.appendChild(chip(badge, badge==='GREEN'?'ok':badge==='AMBER'?'warn':'bad'));
      const v = vkdProxy(r.signals), o = obsProxy(r.signals, r.guards||{});
      pills.appendChild(chip(`Obs ${String((o*100)|0)}%`));
      pills.appendChild(chip(`VKD ${String((v*100)|0)}%`));
      h.appendChild(pills);
      tile.appendChild(h);

      const sub = el('div','subline');
      sub.appendChild(chip('κ '+round3(r.signals?.kappa ?? 0)));
      sub.appendChild(chip('Δhol '+round3(r.signals?.dhol ?? 0)));
      sub.appendChild(chip('Φ* '+round3(r.signals?.phi_star ?? 0)));
      sub.appendChild(chip((r.bytes||0)+'B'));
      box.appendChild(tile).appendChild(sub);
    });
    const cnt = $('#receiptCount'); if (cnt) cnt.textContent = `${list.length} total • showing ${Math.min(25,list.length)}`;
  }

  function renderDeepTable(list){
    const wrap = $('#deepTable'); if (!wrap) return;
    wrap.innerHTML='';
    const tbl = document.createElement('table');
    tbl.style.width='660px'; tbl.style.borderCollapse='collapse'; tbl.style.fontSize='13px';
    const thead = document.createElement('thead');
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
    const tb = document.createElement('tbody');
    list.slice(-200).reverse().forEach(r=>{
      const tr=document.createElement('tr');
      const td=(t,align='left')=>{const d=document.createElement('td'); d.style.padding='8px'; d.style.borderBottom='1px solid var(--line)'; d.style.textAlign=align; d.textContent=t; return d;};
      tr.appendChild(td((r.issued_at||'—').replace('T',' ').replace('Z','')));
      tr.appendChild(td(r.issuer_id||'unknown'));
      tr.appendChild(td(r.model||'—'));
      tr.appendChild(td((r.attrs?.status||'—').toUpperCase()));
      tr.appendChild(td(round3(r.signals?.kappa ?? 0),'right'));
      tr.appendChild(td(round3(r.signals?.dhol ?? 0),'right'));
      tr.appendChild(td(round3(r.signals?.phi_star ?? 0),'right'));
      tr.appendChild(td(String(r.bytes||0),'right'));
      tb.appendChild(tr);
    });
    tbl.appendChild(tb); wrap.appendChild(tbl);
  }

  function renderAll(){
    const stats = computeIssuerStats(RECEIPTS);
    const ov = overallFrom(stats);
    const pl = $('#postureLine'), os = $('#overallScore'), al = $('#adviceLine');
    if (pl) pl.textContent = ov.txt;
    if (os) os.textContent = String(ov.score);
    if (al) al.textContent = ov.worstAdvice;
    renderIssuerCards(stats);
    renderReceipts(RECEIPTS);
  }

  /* ---------- export / summary ---------- */
  function asJsonl(arr){ return arr.map(o=>JSON.stringify(o)).join('\n'); }
  function download(name, data, type='application/octet-stream'){
    const blob = new Blob([data], {type}); const url = URL.createObjectURL(blob);
    const a=document.createElement('a'); a.href=url; a.download=name; document.body.appendChild(a); a.click();
    setTimeout(()=>{ URL.revokeObjectURL(url); a.remove(); },0);
  }
  function clipboard(text){ navigator.clipboard.writeText(text); }

  async function exportPdf(){
    if (!window.PDFLib){
      await new Promise((res,rej)=>{
        const s=document.createElement('script');
        s.src='https://cdn.jsdelivr.net/npm/pdf-lib@1.17.1/dist/pdf-lib.min.js';
        s.onload=res; s.onerror=rej; document.head.appendChild(s);
      });
    }
    const { PDFDocument, StandardFonts } = window.PDFLib;
    const pdfDoc = await PDFDocument.create();
    const page = pdfDoc.addPage([612, 792]);
    const font = await pdfDoc.embedFont(StandardFonts.Helvetica);
    const bold = await pdfDoc.embedFont(StandardFonts.HelveticaBold);

    const stats = computeIssuerStats(RECEIPTS);
    const ov = overallFrom(stats);
    let y = 760;
    page.drawText('OpenLine Wallet — Compliance Summary', {x:40,y, size:16, font:bold}); y-=22;
    page.drawText(new Date().toISOString(), {x:40,y, size:10, font});
    y-=24;
    page.drawText(`Overall health: ${ov.score}`, {x:40,y, size:14, font:bold}); y-=18;
    page.drawText(`Posture: ${ov.txt}`, {x:40,y, size:12, font}); y-=16;
    page.drawText(`Advice: ${ov.worstAdvice}`, {x:40,y, size:12, font}); y-=24;

    page.drawText('Issuer health (7 days)', {x:40,y, size:12, font:bold}); y-=16;
    stats.slice(0,10).forEach(s=>{
      const line = `${s.id.padEnd(10)}  Health ${String(s.health).padStart(3)}  Green ${(s.greenRate*100).toFixed(1)}%  κ95 ${round3(s.k95)}  ΔholTrend ${(s.dholTrend>=0?'+':'')+round3(s.dholTrend)}  Φ*floor ${round3(s.phiFloor)}  Obs ${(s.obsAvg*100|0)}%  VKD ${(s.vkdAvg*100|0)}%`;
      page.drawText(line, {x:40,y, size:10, font}); y-=14;
    });
    y-=10;
    page.drawText('Top 20 receipts (most recent)', {x:40,y, size:12, font:bold}); y-=16;
    RECEIPTS.slice(-20).reverse().forEach(r=>{
      const line = `${(r.issued_at||'—').replace('T',' ').replace('Z','')}  ${r.issuer_id||'unk'}  ${r.model||'—'}  ${(r.attrs?.status||'—').toUpperCase()}  κ ${round3(r.signals?.kappa||0)}  Δ ${round3(r.signals?.dhol||0)}  Φ* ${round3(r.signals?.phi_star||0)}  Obs ${(obsProxy(r.signals,r.guards||{})*100|0)}%  VKD ${(vkdProxy(r.signals)*100|0)}%`;
      page.drawText(line, {x:40,y, size:9, font}); y-=12;
    });

    const pdfBytes = await pdfDoc.save();
    download(`openline-wallet-${Date.now()}.pdf`, pdfBytes, 'application/pdf');
  }

  /* ---------- wire up UI ---------- */
  const hook = (id,fn)=>{ const n=$(id); if(n) n.addEventListener('click',fn); };

  hook('#addRand', ()=> addRandom(1));
  hook('#addRand5', ()=> addRandom(5));
  hook('#addUrl', ()=>{ const u=$('#urlBox')?.value.trim(); if(!u) return; addByUrl(u); $('#urlBox').value=''; });
  hook('#addFile', ()=>{ const f=$('#fileBox')?.files?.[0]; if(!f) return; addFromFile(f); });
  hook('#addPaste', ()=>{ const t=$('#pasteBox')?.value.trim(); if(!t) return; addFromText(t); $('#pasteBox').value=''; });
  hook('#clearAll', ()=>{ if(!confirm('Clear all receipts?')) return; RECEIPTS=[]; renderAll(); });
  hook('#exportJsonl', ()=> download(`receipts-${Date.now()}.jsonl`, asJsonl(RECEIPTS), 'application/jsonl'));
  hook('#copySummary', ()=>{
    const stats = computeIssuerStats(RECEIPTS);
    const ov = overallFrom(stats);
    const lines = [
      `OpenLine Wallet — 7-day summary`,
      `Overall health: ${ov.score} (${ov.txt})`,
      `Advice: ${ov.worstAdvice}`,
      `Issuers:`,
      ...stats.map(s=>`  ${s.id}: health ${s.health}, green ${pct(s.greenRate)}, κ95 ${round3(s.k95)}, ΔholTrend ${(s.dholTrend>=0?'+':'')+round3(s.dholTrend)}, Φ*floor ${round3(s.phiFloor)}, Obs ${(s.obsAvg*100|0)}%, VKD ${(s.vkdAvg*100|0)}%`)
    ];
    clipboard(lines.join('\n')); alert('Summary copied.');
  });
  hook('#exportPdf', exportPdf);
  hook('#toggleDeep', ()=>{
    const w = $('#deepTableWrap'); if (!w) return;
    const show = w.style.display==='none';
    w.style.display = show ? 'block' : 'none';
    if (show) renderDeepTable(RECEIPTS);
  });

  // drag-drop JSON/JSONL
  document.addEventListener('dragover', e=>{e.preventDefault();});
  document.addEventListener('drop', async e=>{
    e.preventDefault();
    const f = e.dataTransfer?.files?.[0]; if(!f) return;
    addFromFile(f);
  });

  /* ---------- boot ---------- */
  (function boot(){
    const q = new URLSearchParams(location.search);
    if (q.get('u')) addByUrl(q.get('u'));
    addRandom(12);
    const wt = $('#windowText'); if (wt) wt.textContent = '7-day posture';
  })();

})();
