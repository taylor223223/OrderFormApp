/* Small chart kit (no libraries): bar, stacked bar, line, horizontal bar, pie / donut, with hover tooltips.
   Usage: <div class="chart" data-chart='{"type":"bar","labels":[...],"series":[{"name":"2026","values":[...]}],"fmt":"money"}'></div>
   window.AISChart.render(el, spec) draws (or redraws) one chart. */
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const PAL = ['#2a78d6', '#eb6834', '#16a34a', '#9333ea', '#e11d48', '#0891b2', '#ca8a04', '#64748b',
               '#db2777', '#4f46e5', '#65a30d', '#b45309'];
  const money = v => (v < 0 ? '-$' : '$') + Math.abs(Math.round(v)).toLocaleString();
  const short = v => { const a = Math.abs(v), s = v < 0 ? '-' : '';
    return a >= 1e6 ? s + '$' + (a / 1e6).toFixed(1) + 'M' : a >= 1000 ? s + '$' + (a / 1000).toFixed(a >= 10000 ? 0 : 1) + 'k' : s + '$' + Math.round(a); };
  const num = v => (Math.round(v * 100) / 100).toLocaleString();
  const shortNum = v => Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + 'k' : num(v);

  function render(box, spec) {
    box.innerHTML = '';
    const isMoney = (spec.fmt || 'money') === 'money';
    const F = isMoney ? money : num, FS = isMoney ? short : shortNum;
    const series = (spec.series || []).map((s, i) => ({...s, color: s.color || PAL[i % PAL.length]}));
    if (!spec.labels || !spec.labels.length || !series.length) { box.innerHTML = '<div class="muted small">No data for this view.</div>'; return; }
    const tip = document.createElement('div'); tip.className = 'ctip';
    const show = (e, html) => { tip.innerHTML = html; tip.style.display = 'block';
      const r = box.getBoundingClientRect(); let left = e.clientX - r.left + 14;
      if (left + 220 > r.width) left = e.clientX - r.left - 230;
      tip.style.left = Math.max(0, left) + 'px'; tip.style.top = (e.clientY - r.top - 10) + 'px'; };
    const hide = () => { tip.style.display = 'none'; };
    const svg = document.createElementNS(NS, 'svg');
    const el = (n, a, p = svg) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); p.appendChild(e); return e; };
    const link = (node, i) => { if (spec.links && spec.links[i]) { node.style.cursor = 'pointer'; node.addEventListener('click', () => { location.href = spec.links[i]; }); } };
    const type = spec.type || 'bar';
    if (type === 'pie' || type === 'donut') {
      const vals = series[0].values.map(v => Math.max(0, v || 0)), total = vals.reduce((a, b) => a + b, 0) || 1;
      const W = 520, H = 260, cx = 130, cy = 130, R = 115, r = type === 'donut' ? 62 : 0;
      svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
      let a0 = -Math.PI / 2;
      const order = vals.map((v, i) => i);
      order.forEach(i => {
        const v = vals[i]; if (!v) return;
        const a1 = a0 + v / total * Math.PI * 2, big = a1 - a0 > Math.PI ? 1 : 0, c = PAL[i % PAL.length];
        const p = (ang, rad) => `${cx + rad * Math.cos(ang)},${cy + rad * Math.sin(ang)}`;
        const d = v >= total - 1e-9 ? `M${cx - R},${cy}a${R},${R} 0 1,0 ${2 * R},0a${R},${R} 0 1,0 ${-2 * R},0` +
            (r ? `M${cx - r},${cy}a${r},${r} 0 1,1 ${2 * r},0a${r},${r} 0 1,1 ${-2 * r},0` : '')
          : r ? `M${p(a0, R)}A${R},${R} 0 ${big},1 ${p(a1, R)}L${p(a1, r)}A${r},${r} 0 ${big},0 ${p(a0, r)}Z`
              : `M${cx},${cy}L${p(a0, R)}A${R},${R} 0 ${big},1 ${p(a1, R)}Z`;
        const s = el('path', {d, fill: c, stroke: '#fff', 'stroke-width': 1.5, 'fill-rule': 'evenodd'});
        const html = `<b>${spec.labels[i]}</b><div>${F(v)} · ${Math.round(v / total * 1000) / 10}%</div>`;
        s.addEventListener('mousemove', e => show(e, html)); s.addEventListener('mouseleave', hide); link(s, i);
        a0 = a1;
      });
      if (r) { el('text', {x: cx, y: cy - 2, 'text-anchor': 'middle', class: 'ax', style: 'font-size:12px'}).textContent = 'Total';
               el('text', {x: cx, y: cy + 16, 'text-anchor': 'middle', style: 'font-size:15px;font-weight:700'}).textContent = FS(total); }
      const n = Math.min(vals.length, 12);
      for (let i = 0; i < n; i++) {
        const y = 22 + i * 19;
        el('rect', {x: 280, y: y - 10, width: 11, height: 11, rx: 2, fill: PAL[i % PAL.length]});
        const t = el('text', {x: 298, y, style: 'font-size:12px'});
        const lab = String(spec.labels[i]); t.textContent = (lab.length > 26 ? lab.slice(0, 25) + '…' : lab) + '  ' + Math.round(vals[i] / total * 100) + '%';
        link(t, i);
      }
      if (vals.length > 12) el('text', {x: 298, y: 22 + 12 * 19, class: 'ax'}).textContent = `+${vals.length - 12} more`;
    } else if (type === 'hbar') {
      const vals = series[0].values, n = vals.length, rowH = 24, W = Math.max(360, Math.round(box.clientWidth || 900)), L = Math.min(W * 0.45, 12 + Math.max(...spec.labels.map(l => String(l).length)) * 6.6), R = 60;
      const H = n * rowH + 8, max = Math.max(1, ...vals.map(v => Math.abs(v || 0)));
      svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
      vals.forEach((v, i) => {
        const y = 4 + i * rowH, w = (W - L - R) * Math.abs(v || 0) / max, lab = String(spec.labels[i]);
        const t = el('text', {x: L - 8, y: y + 15, 'text-anchor': 'end', style: 'font-size:12px'});
        const maxc = Math.floor((L - 10) / 6.6); t.textContent = lab.length > maxc ? lab.slice(0, maxc - 1) + '…' : lab; link(t, i);
        const b = el('rect', {x: L, y: y + 3, width: Math.max(1, w), height: rowH - 8, rx: 3, fill: (v || 0) < 0 ? '#e11d48' : series[0].color});
        el('text', {x: L + w + 6, y: y + 15, class: 'ax'}).textContent = FS(v || 0);
        const html = `<b>${lab}</b><div>${F(v || 0)}</div>` + (spec.extra && spec.extra[i] ? `<div class="muted">${spec.extra[i]}</div>` : '');
        b.addEventListener('mousemove', e => show(e, html)); b.addEventListener('mouseleave', hide); link(b, i);
      });
    } else {
      const W = Math.max(360, Math.round(box.clientWidth || 900)), H = spec.height || 260, L = 60, R = 12, T = 12, B = 30, n = spec.labels.length;
      svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
      const stacked = type === 'stacked';
      let max = 0, min = 0;
      for (let i = 0; i < n; i++) {
        if (stacked) max = Math.max(max, series.reduce((a, s) => a + Math.max(0, s.values[i] || 0), 0));
        else series.forEach(s => { max = Math.max(max, s.values[i] || 0); min = Math.min(min, s.values[i] || 0); });
      }
      max = max || 1;
      const step = Math.pow(10, Math.floor(Math.log10(max - min))), top = Math.ceil(max / step) * step,
            bot = min < 0 ? -Math.ceil(-min / step) * step : 0;
      const y = v => T + (H - T - B) * (1 - (v - bot) / (top - bot)), cw = (W - L - R) / n;
      for (let k = 0; k <= 4; k++) { const v = bot + (top - bot) * k / 4, yy = y(v);
        el('line', {x1: L, x2: W - R, y1: yy, y2: yy, class: 'grid'});
        el('text', {x: L - 6, y: yy + 4, class: 'ax', 'text-anchor': 'end'}).textContent = FS(v); }
      const every = Math.ceil(n / Math.max(4, Math.floor((W - L) / 56)));
      for (let i = 0; i < n; i++) {
        const cx = L + cw * i + cw / 2;
        if (i % every === 0) el('text', {x: cx, y: H - 10, class: 'ax', 'text-anchor': 'middle'}).textContent = String(spec.labels[i]).slice(0, 12);
        if (type === 'line') continue;
        if (stacked) { let acc = 0; const bw = Math.min(40, cw * 0.7);
          series.forEach(s => { const v = Math.max(0, s.values[i] || 0); if (!v) return;
            el('rect', {x: cx - bw / 2, y: y(acc + v), width: bw, height: y(acc) - y(acc + v), fill: s.color}); acc += v; });
        } else { const k = series.length, bw = Math.min(28, cw * 0.8 / k);
          series.forEach((s, j) => { const v = s.values[i] || 0, x = cx - bw * k / 2 + j * bw;
            el('rect', {x: x + 0.5, y: Math.min(y(v), y(0)), width: Math.max(1, bw - 1), height: Math.abs(y(0) - y(v)), rx: 2, fill: s.color}); }); }
      }
      if (type === 'line') series.forEach(s => {
        el('path', {d: s.values.map((v, i) => `${i ? 'L' : 'M'}${L + cw * i + cw / 2},${y(v || 0)}`).join(''), fill: 'none', stroke: s.color, 'stroke-width': 2.5});
        if (n <= 40) s.values.forEach((v, i) => el('circle', {cx: L + cw * i + cw / 2, cy: y(v || 0), r: 3, fill: s.color}));
      });
      el('line', {x1: L, x2: W - R, y1: y(0), y2: y(0), class: 'base'});
      for (let i = 0; i < n; i++) {
        const hit = el('rect', {x: L + cw * i, y: T, width: cw, height: H - T - B, class: 'hit'});
        const tot = series.reduce((a, s) => a + (s.values[i] || 0), 0);
        const html = `<b>${spec.labels[i]}</b>` + series.map(s => `<div><i style="background:${s.color}"></i>${s.name}: <b>${F(s.values[i] || 0)}</b></div>`).join('') +
          (series.length > 1 && stacked ? `<div class="muted">Total ${F(tot)}</div>` : '');
        hit.addEventListener('mousemove', e => show(e, html)); hit.addEventListener('mouseleave', hide); link(hit, i);
      }
    }
    box.appendChild(svg); box.appendChild(tip);
    if (series.length > 1 && type !== 'pie' && type !== 'donut') {
      const lg = document.createElement('div'); lg.className = 'legend small';
      lg.innerHTML = series.map(s => `<span><i style="background:${s.color}"></i>${s.name}</span>`).join(' ');
      box.appendChild(lg);
    }
  }
  window.AISChart = {render, money, PAL};
  document.querySelectorAll('[data-chart]').forEach(b => { try { render(b, JSON.parse(b.dataset.chart)); } catch (e) { console.error(e); } });
})();
