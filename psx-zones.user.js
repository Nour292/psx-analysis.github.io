// ==UserScript==
// @name         PSX Zones
// @namespace    https://nour292.github.io/psx-analysis.github.io/
// @version      1.8
// @description  Copies the app's Buy/Exit zones and draws them on the TradingView chart
// @match        https://nour292.github.io/psx-analysis.github.io/index.html*
// @match        https://www.tradingview.com/chart/*
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        unsafeWindow
// @run-at       document-idle
// @updateURL    https://nour292.github.io/psx-analysis.github.io/psx-zones.user.js
// @downloadURL  https://nour292.github.io/psx-analysis.github.io/psx-zones.user.js
// ==/UserScript==
(function () {
  'use strict';
  const KEY = 'psx_zones';

  // ---------- app page: save the zones the app calculated ----------
  if (location.hostname === 'nour292.github.io') {
    const s = document.createElement('script');
    s.textContent = `(function(){
      const t=setInterval(function(){
        try{
          if(typeof _allRows==='undefined'||!_allRows.length)return;
          const d={};
          _allRows.forEach(function(r){
            const p=r.Plan;if(!p||!r.Symbol)return;
            const f=function(z){return {lo:z.lo,hi:z.hi};};
            d[String(r.Symbol).toUpperCase()]={buy:(p.zv||[]).filter(Boolean).map(f),exit:(p.tz||[]).filter(Boolean).map(f),stop:p.stop,trailingStop:p.trailingStop||null,stopBuy2:p.stopBuy2||null,price:r.Price};
          });
          document.documentElement.setAttribute('data-psx-zones',JSON.stringify(d));
        }catch(e){}
      },1500);
    })();`;
    document.documentElement.appendChild(s);
    setInterval(() => {
      const v = document.documentElement.getAttribute('data-psx-zones');
      if (v) GM_setValue(KEY, JSON.stringify({ at: Date.now(), zones: JSON.parse(v) }));
    }, 2000);
    return;
  }

  // ---------- chart page: draw the saved zones ----------
  const T1 = 1759104000, T2 = 1794268800;
  const f2 = v => (Math.round(v * 100) / 100).toString();
  const MINE = /^(Buy [12]|Exit [12]|Stop |Trailing Stop )/;
  const GREEN = '#10b981', PURPLE = '#a78bfa', RED = '#ef4444';
  let busy = false, lastKey = '';

  async function draw() {
    if (busy) return;
    const api = unsafeWindow.TradingViewApi;
    if (!api) return;
    let chart;
    try { chart = api.activeChart(); } catch (e) { return; }
    if (!chart) return;
    const sym = String(chart.symbol() || '').split(':').pop().toUpperCase();
    let saved;
    try { saved = JSON.parse(GM_getValue(KEY, 'null')); } catch (e) { saved = null; }
    if (!saved || !saved.zones) return;
    const z = saved.zones[sym];
    const price = z && z.price ? parseFloat(z.price) : null;
    const inExit = z && price ? z.exit.findIndex(e=>price>=e.lo&&price<=e.hi) : -1;
    const priceZone = inExit>=0?'exit'+inExit:(price&&z.stop!=null&&price<=z.stop?'below':'normal');
    const key = sym + '|' + JSON.stringify(z || null) + '|' + priceZone;
    const want = z ? Math.min(z.buy.length, 2) + Math.min(z.exit.length, 2) + (z.stop != null ? 1 : 0) : 0;
    let have = 0;
    try {
      for (const sh of chart.getAllShapes()) {
        const e = chart.getShapeById(sh.id);
        if (e && MINE.test(String(e.getProperties().text || ''))) have++;
      }
    } catch (e) { return; }
    if (key === lastKey && have === want) return;
    busy = true;
    const same = () => String(chart.symbol() || '').split(':').pop().toUpperCase() === sym;
    try {
      // remove all shapes that have any text note; leave text-free shapes untouched
      for (const sh of chart.getAllShapes()) {
        const e = chart.getShapeById(sh.id);
        const txt = e && e.getProperties ? String(e.getProperties().text || '') : '';
        if (txt.trim()) chart.removeEntity(sh.id);
      }
      if (z) {
        const box = async (name, a, color) => {
          if (!same()) throw new Error('symbol changed');
          const text = name + '  ' + f2(a.lo) + '–' + f2(a.hi);
          const id = await chart.createMultipointShape(
            [{ time: T1, price: a.lo }, { time: T2, price: a.hi }],
            { shape: 'rectangle', text });
          const e = chart.getShapeById(id);
          e.setProperties({
            color, backgroundColor: color, linewidth: 2, transparency: 62,
            textColor: '#ffffff', fontSize: 10, horzLabelsAlign: 'right', vertLabelsAlign: 'middle', showLabel: true, text
          });
        };
        for (let i = 0; i < z.buy.length && i < 2; i++) await box('Buy ' + (i + 1), z.buy[i], GREEN);
        for (let i = 0; i < z.exit.length && i < 2; i++) await box('Exit ' + (i + 1), z.exit[i], PURPLE);
        {
          let sv=null,sl='';
          if(inExit>=0&&z.trailingStop&&z.trailingStop[inExit]!=null){sv=z.trailingStop[inExit];sl='Trailing Stop '+f2(sv);}
          else if(priceZone==='below'&&z.stopBuy2!=null){sv=z.stopBuy2;sl='Stop (B2) '+f2(sv);}
          else if(z.stop!=null){sv=z.stop;sl='Stop '+f2(sv);}
          if(sv!=null&&same()){const id=await chart.createShape({time:T1,price:sv},{shape:'horizontal_line',text:sl});chart.getShapeById(id).setProperties({linecolor:RED,color:RED,linewidth:1,showLabel:true,text:sl,textColor:'#ffffff'});}
        }
      }
      // label text white (set again after the shapes exist)
      for (const sh of chart.getAllShapes()) {
        const e = chart.getShapeById(sh.id);
        if (MINE.test(String(e.getProperties().text || ''))) e.setProperties({ textColor: '#ffffff' });
      }
      document.documentElement.setAttribute('data-psx-ver', '1.8');
      lastKey = same() ? key : '';
    } catch (e) { console.warn('[PSX Zones]', e); }
    busy = false;
  }
  setInterval(draw, 2000);
})();
