/* Floor plan: rooms + items on their walls. View mode = click for details; Edit mode = build it. */
(function () {
  const D = JSON.parse(document.getElementById('fpdata').textContent);
  const S = 24, NS = 'http://www.w3.org/2000/svg';
  let plan = D.plan, mode = (plan.rooms.length ? 'view' : 'edit'), selRoom = null, selItem = null, dirty = false;
  let nid = 1 + Math.max(0, ...plan.rooms.map(r => r.id), ...plan.items.map(i => i.id));
  const hidden = new Set(); let showBB = true;
  const svg = document.getElementById('plan');
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const el = (n, a, p = svg) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); p.appendChild(e); return e; };
  const R = id => plan.rooms.find(r => r.id === id);
  const CAT = {door: 'Doors', window: 'Windows', patio: 'Windows', bypass: 'Closets', bifold: 'Closets', cab: 'Cabinets'};
  const NAME = {door: 'Door', window: 'Window', patio: 'Sliding patio door', bypass: 'Closet (bypass)', bifold: 'Closet (bi-fold)', cab: 'Cabinets'};
  const inch = v => { const f = +v; if (!f) return ''; const w = Math.floor(f), e = Math.round((f - w) * 8);
    const fr = ['', '1/8', '1/4', '3/8', '1/2', '5/8', '3/4', '7/8'][e % 8] || ''; return (e === 8 ? w + 1 : w) + (fr && e !== 8 ? ' ' + fr : '') + '"'; };

  // ---------------- fields per item type (shown in the details panel)
  const yes = ['', 'Yes', 'No'];
  const FIELDS = {
    door: [['location', 'Inside / outside', ['Interior', 'Exterior']], ['hinge', 'Hinges (seen from this room)', ['Left', 'Right']],
           ['swing', 'Swings', ['in', 'out']], ['height', 'Height (in)'], ['style', 'Style', D.opts.door_styles], ['panels', 'Panels', D.opts.door_panels || ['']],
           ['material', 'Material', D.opts.materials], ['core', 'Core', ['', 'Hollow', 'Solid']], ['finish', 'Color / finish'],
           ['hardware', 'Hardware'], ['notes', 'Notes']],
    window: [['height', 'Height (in)'], ['covering', 'Window covering', ['', 'Horizontal Blind', 'Vertical Blind', 'Roller Shade']],
             ['cov_style', 'Blind / shade style', [''].concat(D.opts.blind_styles)], ['slat', 'Slat size (horizontal)', ['', '1"', '2"']], ['color', 'Color'],
             ['screen', 'Screen', yes], ['notes', 'Notes']],
    patio: [['height', 'Height (in)'], ['config', 'Panel that slides (from outside)', ['', 'XO - Left Slides', 'OX - Right Slides']],
            ['frame', 'Frame', ['', 'Vinyl', 'Aluminum']], ['frame_color', 'Frame color'], ['covering', 'Covering', ['', 'Vertical Blind']],
            ['color', 'Blind color'], ['screen', 'Screen door', yes], ['notes', 'Notes']],
    bypass: [['height', 'Height (in)'], ['size', 'Size', ['standard', 'custom']], ['style', 'Style', ['', 'Flat (smooth)', '2 Panel', '6 Panel', 'Shaker', 'Mirrored']],
             ['finish', 'Finish'], ['notes', 'Notes']],
    cab: [['cab_room', 'Room', ['Kitchen', 'Bathroom Vanity']], ['kind', 'Cabinet', ['Base', 'Sink Base', 'Wall', 'Tall / Pantry', 'Vanity']],
          ['style', 'Door style', [''].concat(D.opts.cab_styles)], ['depth', 'Depth (in)'], ['height', 'Height (in)'],
          ['countertop', 'Countertop', ['', 'Laminate', 'Synthetic', 'Thinscape', 'Custom']], ['notes', 'Notes']],
  };
  FIELDS.bifold = FIELDS.bypass;
  const DEFAULTS = {door: {location: 'Interior', hinge: 'Left', swing: 'in', height: '80'}, window: {}, patio: {height: '80'},
                    bypass: {height: '80', size: 'standard'}, bifold: {height: '80', size: 'standard'}, cab: {cab_room: 'Kitchen', kind: 'Base'}};

  // ---------------- room geometry (feet). Straight walls: offset from the room corner; angled walls: from their start.
  const CORN = ['NW', 'NE', 'SE', 'SW'];
  const WALLNAME = {N: 'Top', E: 'Right', S: 'Bottom', W: 'Left', NW: 'Angled top-left', NE: 'Angled top-right', SE: 'Angled bottom-right', SW: 'Angled bottom-left'};
  const OPENINGS = ['door', 'patio', 'bypass', 'bifold'];
  function walls(r) {
    const {x, y, w, h} = r, c = r.cut || {}, [nw, ne, se, sw] = CORN.map(k => +c[k] || 0);
    const o = {N: [x + nw, y, x + w - ne, y, x, y, 1, 0], E: [x + w, y + ne, x + w, y + h - se, x + w, y, 0, 1],
               S: [x + sw, y + h, x + w - se, y + h, x, y + h, 1, 0], W: [x, y + nw, x, y + h - sw, x, y, 0, 1]};
    const dg = {NW: [x, y + nw, x + nw, y, nw], NE: [x + w - ne, y, x + w, y + ne, ne], SE: [x + w, y + h - se, x + w - se, y + h, se], SW: [x + sw, y + h, x, y + h - sw, sw]};
    for (const k in dg) { const [x1, y1, x2, y2, cut] = dg[k]; if (cut > 0) { const L = Math.hypot(x2 - x1, y2 - y1); o[k] = [x1, y1, x2, y2, x1, y1, (x2 - x1) / L, (y2 - y1) / L]; } }
    return o;
  }
  function poly(r) {
    const {x, y, w, h} = r, c = r.cut || {}, [nw, ne, se, sw] = CORN.map(k => +c[k] || 0);
    return [[x + nw, y], [x + w - ne, y], [x + w, y + ne], [x + w, y + h - se], [x + w - se, y + h], [x + sw, y + h], [x, y + h - sw], [x, y + nw]]
      .filter((p, i, a) => i === 0 || p[0] !== a[i - 1][0] || p[1] !== a[i - 1][1]);
  }
  function wallPt(r, wall, off) {         // pixels: start point, direction, inward normal
    const wl = walls(r)[wall] || walls(r).N, [ox, oy, dx, dy] = wl.slice(4);
    const cx = r.x + r.w / 2, cy = r.y + r.h / 2, px = ox + dx * off, py = oy + dy * off;
    let nx = -dy, ny = dx; if ((cx - px) * nx + (cy - py) * ny < 0) { nx = -nx; ny = -ny; }
    return {x: px * S, y: py * S, dx, dy, nx, ny};
  }
  const segLen = wl => Math.hypot(wl[2] - wl[0], wl[3] - wl[1]);
  function overlap(sg, wl) {
    const L = segLen(wl); if (L < 1e-6) return 0;
    const ux = (wl[2] - wl[0]) / L, uy = (wl[3] - wl[1]) / L, off = (px, py) => Math.abs((px - wl[0]) * uy - (py - wl[1]) * ux);
    if (off(sg[0], sg[1]) > .15 || off(sg[2], sg[3]) > .15) return 0;
    const a = (sg[0] - wl[0]) * ux + (sg[1] - wl[1]) * uy, b = (sg[2] - wl[0]) * ux + (sg[3] - wl[1]) * uy;
    return Math.max(0, Math.min(L, Math.max(a, b)) - Math.max(0, Math.min(a, b)));
  }
  function itemSeg(it) {
    const r = R(it.room), wl = r && walls(r)[it.wall]; if (!wl) return null;
    const [ox, oy, dx, dy] = wl.slice(4), a = it.off, b = it.off + it.w / 12;
    return [ox + dx * a, oy + dy * a, ox + dx * b, oy + dy * b];
  }
  function baseboard(r) {                 // linear feet: closed walls minus door/closet openings and base cabinets
    if (r.bb === false) return 0;
    const segs = plan.items.map(it => [it, itemSeg(it)]); let lf = 0;
    for (const [k, wl] of Object.entries(walls(r))) {
      if ((r.open || []).includes(k)) continue;
      let cut = 0;
      segs.forEach(([it, sg]) => { if (sg && (OPENINGS.includes(it.t) || (it.t === 'cab' && it.room === r.id && (it.p || {}).kind !== 'Wall'))) cut += overlap(sg, wl); });
      lf += Math.max(0, segLen(wl) - cut);
    }
    return Math.round(lf * 10) / 10;
  }

  // ---------------- drawing
  function draw() {
    svg.innerHTML = '';
    $('emptyMsg').style.display = plan.rooms.length || mode === 'edit' ? 'none' : 'block';
    const W = Math.max(30, ...plan.rooms.map(r => r.x + r.w + 1)), H = Math.max(20, ...plan.rooms.map(r => r.y + r.h + 1));
    svg.setAttribute('viewBox', `-20 -20 ${W * S + 40} ${H * S + 40}`);
    const g = el('g', {stroke: '#dbe7f3', 'stroke-width': 1});
    for (let i = 0; i <= W; i++) el('line', {x1: i * S, y1: 0, x2: i * S, y2: H * S}, g);
    for (let j = 0; j <= H; j++) el('line', {x1: 0, y1: j * S, x2: W * S, y2: j * S}, g);
    const solid = [];
    plan.rooms.forEach(r => {
      const sel = r.id === selRoom && !selItem;
      const gr = el('g', {class: 'fproom' + (sel ? ' sel' : '') + (mode === 'edit' ? ' movable' : '')});
      const hall = r.kind === 'hall';
      el('polygon', {class: 'body', points: poly(r).map(p => p[0] * S + ',' + p[1] * S).join(' '), fill: hall ? '#eef2f7d0' : '#ffffffc0', stroke: 'none'}, gr);
      for (const [k, wl] of Object.entries(walls(r))) {
        if ((r.open || []).includes(k)) el('line', {class: 'wall', x1: wl[0] * S, y1: wl[1] * S, x2: wl[2] * S, y2: wl[3] * S, stroke: '#94a3b8', 'stroke-width': 1.5, 'stroke-dasharray': '6 5'}, gr);
        else solid.push([wl, sel]);
      }
      // labels: narrow rooms (hallways) get their label turned to run along the long side
      const tall = r.h > r.w * 1.6, lw = tall ? r.h : r.w, sw = tall ? r.w : r.h;
      const fs = Math.min(15, Math.min(lw * 1.1, sw * 2.6)), cx = (r.x + r.w / 2) * S, cy = (r.y + r.h / 2) * S;
      const lg = el('g', {transform: tall ? `rotate(-90 ${cx} ${cy})` : ''}, gr), y0 = cy - (showBB && r.bb !== false ? fs * .6 : 0);
      const t = el('text', {x: cx, y: y0, 'text-anchor': 'middle', 'font-size': fs, 'font-weight': 700, fill: '#334155'}, lg); t.textContent = r.n;
      const sub = Math.max(8, fs - 3);
      const d = el('text', {x: cx, y: y0 + sub + 3, 'text-anchor': 'middle', 'font-size': sub, fill: '#64748b'}, lg); d.textContent = `${ft(r.w)} x ${ft(r.h)}`;
      if (showBB && r.bb !== false) { const b = el('text', {x: cx, y: y0 + 2 * sub + 6, 'text-anchor': 'middle', 'font-size': sub, fill: '#0f766e', 'font-weight': 700}, lg);
        b.textContent = `Baseboard ${baseboard(r)} LF`; }
      gr.addEventListener('pointerdown', e => roomDown(e, r));
    });
    const wg = el('g', {'pointer-events': 'none'});           // solid walls on top, so an open side never hides a real wall
    solid.sort((a, b) => a[1] - b[1]).forEach(([wl, sel]) => el('line', {x1: wl[0] * S, y1: wl[1] * S, x2: wl[2] * S, y2: wl[3] * S,
      stroke: sel ? '#ea580c' : '#1f2937', 'stroke-width': 5, 'stroke-linecap': 'square'}, wg));
    plan.items.forEach(it => { if (!hidden.has(CAT[it.t])) drawItem(it); });
    sched();
  }
  const ft = v => { const f = Math.floor(v), i = Math.round((v - f) * 12); return i ? `${f}' ${i}"` : `${f}'`; };

  function drawItem(it) {
    const r = R(it.room); if (!r || !walls(r)[it.wall]) return;
    const p = wallPt(r, it.wall, it.off), L = it.w / 12 * S, P = it.p || {};
    const ang = Math.atan2(p.dy, p.dx) * 180 / Math.PI, flip = (-Math.sin(ang * Math.PI / 180) * p.nx + Math.cos(ang * Math.PI / 180) * p.ny) < 0;
    const g = el('g', {class: 'fpitem' + (it.id === selItem ? ' sel' : ''), transform: `translate(${p.x} ${p.y}) rotate(${ang})${flip ? ' scale(1 -1)' : ''}`});
    g.addEventListener('pointerdown', e => { e.stopPropagation(); selItem = it.id; selRoom = it.room; draw(); panel(); });
    // local frame: x along the wall (0..L), +y into the room
    el('line', {x1: 0, y1: 0, x2: L, y2: 0, stroke: '#f7fbff', 'stroke-width': 8}, g);
    if (it.t === 'door') {
      const s = (P.swing || 'in') === 'in' ? 1 : -1, left = (P.hinge || 'Left') === 'Left';
      const hx = left ? 0 : L, fx = left ? L : 0;
      el('line', {x1: hx, y1: 0, x2: hx, y2: L * s, stroke: '#92400e', 'stroke-width': 3}, g);
      const cross = (fx - hx) * (L * s);
      el('path', {d: `M ${hx} ${L * s} A ${L} ${L} 0 0 ${cross > 0 ? 0 : 1} ${fx} 0`, fill: 'none', stroke: '#92400e', 'stroke-dasharray': '4 3'}, g);
      el('rect', {class: 'hit', x: 0, y: s > 0 ? 0 : -L, width: L, height: Math.max(10, L), fill: 'transparent'}, g);
    } else if (it.t === 'window' || it.t === 'patio') {
      el('rect', {class: 'hit', x: 0, y: -5, width: L, height: 10, fill: '#e0f2fe', stroke: '#0369a1', 'stroke-width': 1.5}, g);
      if (it.t === 'patio') el('line', {x1: L / 2, y1: -5, x2: L / 2, y2: 5, stroke: '#0369a1', 'stroke-width': 1.5}, g);
    } else {
      const dep = (it.t === 'cab' ? (+P.depth || 24) / 12 : 2) * S, cab = it.t === 'cab', upper = cab && P.kind === 'Wall';
      el('rect', {class: 'hit', x: 0, y: 0, width: L, height: dep, fill: cab ? (upper ? 'transparent' : '#ede9fe') : '#fef3c7',
                  stroke: cab ? '#6d28d9' : '#92400e', 'stroke-width': 1.5, 'stroke-dasharray': upper ? '6 4' : ''}, g);
      if (it.t === 'bypass') {
        el('line', {x1: 0, y1: dep * .35, x2: L * .55, y2: dep * .35, stroke: '#92400e', 'stroke-width': 3}, g);
        el('line', {x1: L * .45, y1: dep * .6, x2: L, y2: dep * .6, stroke: '#92400e', 'stroke-width': 3}, g);
      }
      if (it.t === 'bifold') for (let i = 0; i < 4; i++) { const a = i / 4 * L, b = (i + .5) / 4 * L, c = (i + 1) / 4 * L;
        el('polyline', {points: `${a},0 ${b},${dep * .7} ${c},0`, fill: 'none', stroke: '#92400e', 'stroke-width': 1.5}, g); }
      if (cab && P.kind === 'Sink Base') el('rect', {x: L * .3, y: dep * .2, width: L * .4, height: dep * .6, rx: 5, fill: '#fff', stroke: '#6d28d9'}, g);
    }
  }

  // ---------------- dragging rooms (edit mode)
  let drag = null;
  function toSvg(e) { const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY; return pt.matrixTransform(svg.getScreenCTM().inverse()); }
  function roomDown(e, r) {
    selRoom = r.id; selItem = null; panel();
    if (mode === 'edit') { const pt = toSvg(e); drag = {r, dx: pt.x / S - r.x, dy: pt.y / S - r.y}; svg.setPointerCapture(e.pointerId); }
    draw();
  }
  svg.addEventListener('pointermove', e => {
    if (!drag) return; const pt = toSvg(e), r = drag.r;
    let nx = Math.round((pt.x / S - drag.dx) * 2) / 2, ny = Math.round((pt.y / S - drag.dy) * 2) / 2;
    plan.rooms.forEach(o => { if (o === r) return;
      [o.x + o.w, o.x - r.w, o.x, o.x + o.w - r.w].forEach(v => { if (Math.abs(nx - v) < .8) nx = v; });
      [o.y + o.h, o.y - r.h, o.y, o.y + o.h - r.h].forEach(v => { if (Math.abs(ny - v) < .8) ny = v; }); });
    r.x = Math.max(0, nx); r.y = Math.max(0, ny); dirty = true; draw();
  });
  svg.addEventListener('pointerup', () => { drag = null; });
  svg.addEventListener('pointerdown', e => { if (e.target === svg || (e.target.tagName === 'line' && !e.target.closest('g.fproom, g.fpitem'))) { selRoom = null; selItem = null; draw(); panel(); } });

  // ---------------- details panel
  function photoUrl(it) {
    const P = it.p || {}, u = (g, n) => D.photo.replace('/G/', '/' + encodeURIComponent(g) + '/').replace(/\/N$/, '/' + encodeURIComponent(n));
    if (it.t === 'door' && P.style) return u('door_style', P.style.startsWith('Flat') ? 'HC Primecoat' : P.style);
    if (it.t === 'cab' && P.style) return u('cabinet_style', P.style);
    if ((it.t === 'bypass' || it.t === 'bifold') && P.style && P.style !== 'Mirrored') return u('door_style', P.style === 'Flat (smooth)' ? 'HC Primecoat' : P.style);
    if (it.t === 'window' && P.covering === 'Roller Shade') return u('product', 'roller-shade');
    return null;
  }
  function blindPic(it) {
    const P = it.p || {}, vert = it.t === 'patio' || P.covering === 'Vertical Blind';
    if (!P.covering) return '';
    return `<svg viewBox="0 0 200 110" class="fpthumb"><rect x="20" y="8" width="160" height="96" fill="#e0f2fe" stroke="#64748b"/>
      ${vert ? Array.from({length: 14}, (_, i) => `<rect x="${24 + i * 11}" y="14" width="9" height="88" fill="#f5f0e6" stroke="#cbd5e1"/>`).join('')
             : Array.from({length: 12}, (_, i) => `<rect x="24" y="${14 + i * 7.4}" width="152" height="5" fill="#fafafa" stroke="#cbd5e1"/>`).join('')}</svg>`;
  }
  function startForm(it) {
    if (it.t === 'window') { const c = D.covering[(it.p || {}).covering]; return c || 'window_screen'; }
    return D.form_for[it.t];
  }
  function panel() {
    const box = $('props'); $('roomHint').textContent = selRoom ? 'Adding to: ' + (R(selRoom) || {}).n : 'Click a room first.';
    const fw = $('fWall'), keep = fw.value, ks = selRoom && R(selRoom) ? Object.keys(walls(R(selRoom))) : ['N', 'E', 'S', 'W'];
    fw.innerHTML = ks.map(k => `<option value="${k}">${WALLNAME[k]}</option>`).join(''); if (ks.includes(keep)) fw.value = keep;
    if (selItem) {
      const it = plan.items.find(i => i.id === selItem); if (!it) { selItem = null; return panel(); }
      const r = R(it.room), P = it.p || {}, purl = photoUrl(it);
      if (mode === 'view') {
        const rows = [['Where', `${r.n}, ${WALLNAME[it.wall].toLowerCase()} wall`], ['Width', inch(it.w)]]
          .concat(FIELDS[it.t].filter(f => P[f[0]]).map(f => [f[1], f[0] === 'height' || f[0] === 'depth' ? inch(P[f[0]]) : P[f[0]]]));
        box.innerHTML = `<div class="muted small">${CAT[it.t]}</div><h3 style="margin:2px 0 8px">${esc(NAME[it.t])}${P.location === 'Exterior' ? ' (exterior)' : ''}</h3>
          ${purl ? `<img class="fpthumb" src="${purl}" onerror="this.remove()" alt="">` : blindPic(it)}
          <div class="kv">${rows.map(([k, v]) => `<div>${esc(k)}</div><div><b>${esc(v)}</b></div>`).join('')}</div>
          <div class="row" style="gap:6px;flex-wrap:wrap"><a class="btn sm primary" href="${D.editor}?form=${startForm(it)}&customer=${D.cid}">Start order</a>
          <button class="btn sm" type="button" id="toEdit">Edit</button><a class="btn sm" href="${D.forms}">Send customer form</a></div>`;
        $('toEdit').onclick = () => setMode('edit');
      } else {
        const f = FIELDS[it.t].map(([k, lab, opts]) => opts
          ? `<label>${lab}</label><select data-k="${k}">${opts.map(o => `<option ${o === (P[k] || '') ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`
          : `<label>${lab}</label><input data-k="${k}" value="${esc(P[k] || '')}">`).join('');
        box.innerHTML = `<h3 style="margin-top:0">${esc(NAME[it.t])} <span class="muted small">in ${esc(r.n)}</span></h3>
          <div class="grid g2"><div><label>Width (in)</label><input data-g="w" type="number" step="0.125" value="${it.w}"></div>
          <div><label>From corner (ft)</label><input data-g="off" type="number" step="0.5" value="${it.off}"></div></div>
          <label>Wall</label><select data-g="wall">${Object.keys(walls(r)).map(w => `<option value="${w}" ${w === it.wall ? 'selected' : ''}>${WALLNAME[w]}</option>`).join('')}</select>
          ${f}${purl ? `<img class="fpthumb" style="margin-top:8px" src="${purl}" onerror="this.remove()" alt="">` : ''}
          <button class="btn sm danger" type="button" id="rmItem" style="margin-top:8px">Remove</button>`;
        box.querySelectorAll('[data-g]').forEach(i => i.onchange = () => { const k = i.dataset.g; it[k] = k === 'wall' ? i.value : +i.value; dirty = true; draw(); });
        box.querySelectorAll('[data-k]').forEach(i => i.onchange = () => { it.p = it.p || {}; it.p[i.dataset.k] = i.value; dirty = true; draw(); panel(); });
        $('rmItem').onclick = () => { plan.items = plan.items.filter(x => x !== it); selItem = null; dirty = true; draw(); panel(); };
      }
    } else if (selRoom && mode === 'edit') {
      const r = R(selRoom), c = r.cut || {}, open = r.open || [];
      const cornerIn = (k, lab) => `<div><label>${lab}</label><input data-cut="${k}" type="number" min="0" step="0.5" value="${c[k] || ''}" placeholder="0"></div>`;
      box.innerHTML = `<h3 style="margin-top:0">${esc(r.n)}</h3><label>Name</label><input id="rn" value="${esc(r.n)}">
        <div class="grid g2"><div><label>Width (ft)</label><input id="rw" type="number" step="0.5" value="${r.w}"></div>
        <div><label>Length (ft)</label><input id="rh" type="number" step="0.5" value="${r.h}"></div></div>
        <label>Type</label><select id="rk"><option value="room">Room</option><option value="hall" ${r.kind === 'hall' ? 'selected' : ''}>Hallway</option></select>
        <h4 style="margin:10px 0 2px">Angled corners</h4><div class="muted small">How far to cut the corner (ft). Makes a 45° wall you can put a door on.</div>
        <div class="grid g2">${cornerIn('NW', 'Top-left')}${cornerIn('NE', 'Top-right')}${cornerIn('SW', 'Bottom-left')}${cornerIn('SE', 'Bottom-right')}</div>
        <h4 style="margin:10px 0 2px">Open sides (no wall)</h4><div class="muted small">For a hall or kitchen that opens into the next room. No baseboard there.</div>
        <div class="row" style="gap:10px;flex-wrap:wrap">${Object.keys(walls(r)).map(k => `<label class="chk"><input type="checkbox" data-open="${k}" ${open.includes(k) ? 'checked' : ''}> ${WALLNAME[k]}</label>`).join('')}</div>
        <label class="chk" style="margin-top:8px"><input type="checkbox" id="rbb" ${r.bb === false ? '' : 'checked'}> Baseboard in this room <b>(${baseboard(r)} LF)</b></label>
        <div class="muted small" style="margin-top:6px">${plan.items.filter(i => i.room === r.id).length} item(s) on this room's walls.</div>
        <button class="btn sm danger" type="button" id="rmRoom" style="margin-top:8px">Delete room</button>`;
      const ch = () => { dirty = true; draw(); panel(); };
      $('rn').onchange = e => { r.n = e.target.value; ch(); };
      $('rw').onchange = e => { r.w = Math.max(1, +e.target.value); ch(); };
      $('rh').onchange = e => { r.h = Math.max(1, +e.target.value); ch(); };
      $('rk').onchange = e => { if (e.target.value === 'hall') r.kind = 'hall'; else delete r.kind; ch(); };
      $('rbb').onchange = e => { if (e.target.checked) delete r.bb; else r.bb = false; ch(); };
      box.querySelectorAll('[data-cut]').forEach(i => i.onchange = () => {
        const v = Math.min(Math.min(r.w, r.h) / 2, Math.max(0, +i.value || 0)); r.cut = Object.assign({}, r.cut);
        if (v) r.cut[i.dataset.cut] = v; else { delete r.cut[i.dataset.cut]; plan.items = plan.items.filter(t => !(t.room === r.id && t.wall === i.dataset.cut)); }
        if (!Object.keys(r.cut).length) delete r.cut; ch(); });
      box.querySelectorAll('[data-open]').forEach(i => i.onchange = () => {
        const o = new Set(r.open || []); i.checked ? o.add(i.dataset.open) : o.delete(i.dataset.open); r.open = [...o]; if (!r.open.length) delete r.open; ch(); });
      $('rmRoom').onclick = () => { if (!confirm('Delete ' + r.n + ' and everything on its walls?')) return;
        plan.rooms = plan.rooms.filter(x => x !== r); plan.items = plan.items.filter(i => i.room !== r.id); selRoom = null; dirty = true; draw(); panel(); };
    } else {
      box.innerHTML = `<h3 style="margin-top:0">Click an item</h3><div class="muted small">${mode === 'edit'
        ? 'Add rooms on the left, drag them into place, then click a room and add doors, windows, closets and cabinets to its walls.'
        : 'Doors, windows, closets and cabinets show their photo, sizes and colors here.'}</div>`;
    }
  }
  function sched() {
    const rows = plan.items.slice().sort((a, b) => (CAT[a.t] + a.room).localeCompare(CAT[b.t] + b.room));
    $('sched').innerHTML = rows.length ? '<tr><th>Item</th><th>Room</th><th>Size</th></tr>' + rows.map(it => {
      const P = it.p || {}, r = R(it.room) || {};
      const size = inch(it.w) + (P.height ? ' x ' + inch(P.height) : '');
      const what = NAME[it.t] + (P.style ? ' · ' + P.style : P.covering ? ' · ' + P.covering : '');
      return `<tr class="${it.id === selItem ? 'on' : ''}" data-id="${it.id}"><td>${esc(what)}</td><td>${esc(r.n)}</td><td>${size}</td></tr>`;
    }).join('') : '<tr><td class="muted">Nothing on the plan yet.</td></tr>';
    const bb = plan.rooms.filter(r => r.bb !== false).map(r => [r, baseboard(r)]).filter(x => x[1] > 0);
    if (bb.length) $('sched').innerHTML += '<tr><th>Baseboard</th><th>Room</th><th>LF</th></tr>' +
      bb.map(([r, lf]) => `<tr data-room="${r.id}"><td>Baseboard</td><td>${esc(r.n)}</td><td>${lf}</td></tr>`).join('') +
      `<tr><td><b>Baseboard total</b></td><td></td><td><b>${Math.round(bb.reduce((a, x) => a + x[1], 0) * 10) / 10}</b></td></tr>`;
    $('sched').querySelectorAll('tr[data-room]').forEach(tr => tr.onclick = () => { selRoom = +tr.dataset.room; selItem = null; draw(); panel(); });
    $('sched').querySelectorAll('tr[data-id]').forEach(tr => tr.onclick = () => { selItem = +tr.dataset.id; selRoom = (plan.items.find(i => i.id === selItem) || {}).room; draw(); panel(); });
  }

  // ---------------- tools
  $('addRoom').onclick = () => {
    const w = +$('rW').value || 10, h = +$('rL').value || 10;
    const maxX = plan.rooms.length ? Math.max(...plan.rooms.map(r => r.x + r.w)) : 0;
    const r = {id: nid++, n: $('rName').value || 'Room', x: maxX, y: 0, w, h};
    if ($('rKind').value === 'hall') r.kind = 'hall';
    plan.rooms.push(r); selRoom = r.id; selItem = null; dirty = true; draw(); panel();
  };
  $('fType').onchange = e => { $('fW').value = {door: 32, window: 36, patio: 72, bypass: 72, bifold: 48, cab: 96}[e.target.value]; };
  $('addFeat').onclick = () => {
    if (!selRoom) { alert('Click a room on the plan first.'); return; }
    const t = $('fType').value;
    const it = {id: nid++, room: selRoom, t, wall: $('fWall').value, off: +$('fOff').value || 0, w: +$('fW').value || 32, p: Object.assign({}, DEFAULTS[t])};
    if (it.wall.length === 2) it.off = Math.max(0, Math.round((segLen(walls(R(selRoom))[it.wall]) - it.w / 12) / 2 * 4) / 4);   // angled: centered
    plan.items.push(it); selItem = it.id; dirty = true; draw(); panel();
  };
  function setMode(m) {
    mode = m; $('mView').classList.toggle('on', m === 'view'); $('mEdit').classList.toggle('on', m === 'edit');
    $('fpwrap').classList.toggle('editing', m === 'edit'); draw(); panel();
  }
  $('mView').onclick = () => setMode('view'); $('mEdit').onclick = () => setMode('edit');
  $('saveBtn').onclick = async () => {
    const r = await fetch(D.save, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': CSRF}, body: JSON.stringify(plan)});
    const j = await r.json().catch(() => ({}));
    if (j.ok) { dirty = false; $('savedMsg').textContent = `Saved. ${j.measurements} item(s) now on file as this floorplan's measurements.`; }
    else $('savedMsg').textContent = 'Could not save - try again.';
  };
  window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  ['Doors', 'Windows', 'Closets', 'Cabinets'].forEach(c => { const b = document.createElement('button'); b.type = 'button'; b.className = 'btn sm'; b.textContent = c;
    b.onclick = () => { hidden.has(c) ? hidden.delete(c) : hidden.add(c); b.classList.toggle('off'); draw(); }; $('filters').appendChild(b); });
  { const b = document.createElement('button'); b.type = 'button'; b.className = 'btn sm'; b.textContent = 'Baseboard';
    b.onclick = () => { showBB = !showBB; b.classList.toggle('off'); draw(); }; $('filters').appendChild(b); }
  setMode(mode);
})();
