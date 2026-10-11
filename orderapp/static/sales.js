/* Sales by month: this year vs last year (grouped bars), goal ticks, hover tooltip. */
(function () {
  const box = document.getElementById('monthChart'), src = document.getElementById('chartdata');
  if (!box || !src) return;
  const D = JSON.parse(src.textContent), NS = 'http://www.w3.org/2000/svg';
  const W = 900, H = 260, L = 56, R = 10, T = 10, B = 26;
  const max = Math.max(1, ...D.this, ...D.last, ...D.goal.map(g => g || 0));
  const step = Math.pow(10, Math.floor(Math.log10(max))), nice = Math.ceil(max / step) * step;
  const y = v => T + (H - T - B) * (1 - v / nice), cw = (W - L - R) / 12, bw = Math.min(22, cw * 0.3);
  const svg = document.createElementNS(NS, 'svg'); svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', `Sales by month, ${D.y} vs ${D.y - 1}`);
  const el = (n, a, p = svg) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); p.appendChild(e); return e; };
  const fmt = v => '$' + Math.round(v).toLocaleString();
  const short = v => v >= 1000 ? '$' + (v / 1000).toFixed(v >= 10000 ? 0 : 1) + 'k' : '$' + Math.round(v);
  for (let i = 0; i <= 4; i++) {
    const v = nice * i / 4, yy = y(v);
    el('line', {x1: L, x2: W - R, y1: yy, y2: yy, class: 'grid'});
    el('text', {x: L - 6, y: yy + 4, class: 'ax', 'text-anchor': 'end'}).textContent = short(v);
  }
  // rounded top, square base (anchored to the baseline)
  const bar = (x, v, cls) => { const h = Math.max(0, y(0) - y(v)); if (!h) return;
    const r = Math.min(4, h, bw / 2), x2 = x + bw, yt = y(v), yb = y(0);
    el('path', {d: `M${x},${yb}V${yt + r}Q${x},${yt} ${x + r},${yt}H${x2 - r}Q${x2},${yt} ${x2},${yt + r}V${yb}Z`, class: cls}); };
  D.months.forEach((m, i) => {
    const cx = L + cw * i + cw / 2;
    bar(cx - bw - 1, D.this[i], 'b1'); bar(cx + 1, D.last[i], 'b2');
    if (D.goal[i]) el('line', {x1: cx - bw - 5, x2: cx + bw + 5, y1: y(D.goal[i]), y2: y(D.goal[i]), class: 'goal'});
    el('text', {x: cx, y: H - 8, class: 'ax', 'text-anchor': 'middle'}).textContent = m;
    const hit = el('rect', {x: L + cw * i, y: T, width: cw, height: H - T - B, class: 'hit'});
    hit.addEventListener('mouseenter', e => show(i, e)); hit.addEventListener('mousemove', e => show(i, e));
    hit.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
  });
  el('line', {x1: L, x2: W - R, y1: y(0), y2: y(0), class: 'base'});
  box.appendChild(svg);
  const tip = document.createElement('div'); tip.className = 'ctip'; box.appendChild(tip);
  function show(i, e) {
    const ch = D.last[i] ? Math.round((D.this[i] - D.last[i]) / D.last[i] * 100) : null;
    tip.innerHTML = `<b>${D.months[i]}</b><div><i class="k1"></i>${D.y}: <b>${fmt(D.this[i])}</b></div>
      <div><i class="k2"></i>${D.y - 1}: <b>${fmt(D.last[i])}</b></div>` +
      (ch === null ? '' : `<div class="muted">${ch >= 0 ? '▲' : '▼'} ${Math.abs(ch)}% vs last year</div>`) +
      (D.goal[i] ? `<div class="muted">Goal ${fmt(D.goal[i])}</div>` : '');
    const r = box.getBoundingClientRect();
    tip.style.display = 'block';
    let left = e.clientX - r.left + 14; if (left + 190 > r.width) left = e.clientX - r.left - 200;
    tip.style.left = left + 'px'; tip.style.top = (e.clientY - r.top - 10) + 'px';
  }
})();
