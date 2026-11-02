// ==UserScript==
// @name         PSX Zones
// @namespace    https://nour292.github.io/psx-analysis.github.io/
// @version      1.2
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
            d[String(r.Symbol).toUpperCase()]={buy:(p.zv||[]).filter(Boolean).map(f),exit:(p.tz||[]).filter(Boolean).map(f),stop:p.stop};
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
  const MINE = /^(Buy [12]|Exit [12]|Stop )/;
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
    const key = sym + '|' + saved.at;
    if (key === lastKey) return;
    busy = true;
    try {
      // remove only our own old shapes
      for (const sh of chart.getAllShapes()) {
        const e = chart.getShapeById(sh.id);
        const txt = e && e.getProperties ? String(e.getProperties().text || '') : '';
        if (MINE.test(txt)) chart.removeEntity(sh.id);
      }
      if (z) {
        const box = async (name, a, color) => {
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
        if (z.stop != null) {
          const id = await chart.createShape({ time: T1, price: z.stop }, { shape: 'horizontal_line', text: 'Stop ' + f2(z.stop) });
          chart.getShapeById(id).setProperties({ linecolor: RED, color: RED, linewidth: 1, showLabel: true, text: 'Stop ' + f2(z.stop), textColor: '#ffffff' });
        }
      }
      lastKey = key;
    } catch (e) { console.warn('[PSX Zones]', e); }
    busy = false;
  }
  setInterval(draw, 2000);
})();
