/* Floor plan: rooms + items on their walls. View mode = click for details; Edit mode = build it. */
(function () {
  const D = JSON.parse(document.getElementById('fpdata').textContent);
  const S = 24, NS = 'http://www.w3.org/2000/svg';
  let plan = D.plan, mode = (plan.rooms.length ? 'view' : 'edit'), selRoom = null, selItem = null, dirty = false;
  let nid = 1 + Math.max(0, ...plan.rooms.map(r => r.id), ...plan.items.map(i => i.id));
  const hidden = new Set();
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
           ['swing', 'Swings', ['in', 'out']], ['height', 'Height (in)'], ['style', 'Style', D.opts.door_styles],
           ['material', 'Material', D.opts.materials], ['core', 'Core', ['', 'Hollow', 'Solid']], ['finish', 'Color / finish'],
           ['hardware', 'Hardware'], ['notes', 'Notes']],
    window: [['height', 'Height (in)'], ['covering', 'Window covering', ['', 'Horizontal Blind', 'Vertical Blind', 'Roller Shade']],
             ['cov_style', 'Blind / shade style', [''].concat(D.opts.blind_styles)], ['color', 'Color'], ['depth', 'Opening depth (in)'],
             ['screen', 'Screen', yes], ['notes', 'Notes']],
    patio: [['height', 'Height (in)'], ['config', 'Panel that slides (from outside)', ['', 'XO - Left Slides', 'OX - Right Slides']],
            ['frame', 'Frame', ['', 'Vinyl', 'Aluminum']], ['frame_color', 'Frame color'], ['covering', 'Covering', ['', 'Vertical Blind']],
            ['color', 'Blind color'], ['screen', 'Screen door', yes], ['notes', 'Notes']],
    bypass: [['height', 'Height (in)'], ['size', 'Size', ['standard', 'custom']], ['style', 'Style', ['', 'Flat (smooth)', '2 Panel', '6 Panel', 'Shaker', 'Mirrored']],
             ['finish', 'Finish'], ['notes', 'Notes']],
    cab: [['cab_room', 'Room', ['Kitchen', 'Bathroom Vanity']], ['kind', 'Cabinet', ['Base', 'Sink Base', 'Wall', 'Tall / Pantry', 'Vanity']],
          ['style', 'Door style', [''].concat(D.opts.cab_styles)], ['depth', 'Depth (in)'], ['height', 'Height (in)'],
          ['countertop', 'Countertop', ['', 'Laminate', 'Synthetic', 'Custom']], ['notes', 'Notes']],
  };
  FIELDS.bifold = FIELDS.bypass;
  const DEFAULTS = {door: {location: 'Interior', hinge: 'Left', swing: 'in', height: '80'}, window: {}, patio: {height: '80'},
                    bypass: {height: '80', size: 'standard'}, bifold: {height: '80', size: 'standard'}, cab: {cab_room: 'Kitchen', kind: 'Base'}};

  function wallPt(r, wall, off) {
    if (wall === 'N') return {x: (r.x + off) * S, y: r.y * S, dx: 1, dy: 0, nx: 0, ny: 1};
    if (wall === 'S') return {x: (r.x + off) * S, y: (r.y + r.h) * S, dx: 1, dy: 0, nx: 0, ny: -1};
    if (wall === 'W') return {x: r.x * S, y: (r.y + off) * S, dx: 0, dy: 1, nx: 1, ny: 0};
    return {x: (r.x + r.w) * S, y: (r.y + off) * S, dx: 0, dy: 1, nx: -1, ny: 0};
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
    plan.rooms.forEach(r => {
      const gr = el('g', {class: 'fproom' + (r.id === selRoom && !selItem ? ' sel' : '') + (mode === 'edit' ? ' movable' : '')});
      el('rect', {class: 'body', x: r.x * S, y: r.y * S, width: r.w * S, height: r.h * S, fill: '#ffffffc0', stroke: '#1f2937', 'stroke-width': 5}, gr);
      const fs = Math.min(15, r.w * 2.2);
      const t = el('text', {x: (r.x + r.w / 2) * S, y: (r.y + r.h / 2) * S, 'text-anchor': 'middle', 'font-size': fs, 'font-weight': 700, fill: '#334155'}, gr); t.textContent = r.n;
      const d = el('text', {x: (r.x + r.w / 2) * S, y: (r.y + r.h / 2) * S + fs + 2, 'text-anchor': 'middle', 'font-size': Math.max(9, fs - 3), fill: '#64748b'}, gr);
      d.textContent = `${ft(r.w)} x ${ft(r.h)}`;
      gr.addEventListener('pointerdown', e => roomDown(e, r));
    });
    plan.items.forEach(it => { if (!hidden.has(CAT[it.t])) drawItem(it); });
    sched();
  }
  const ft = v => { const f = Math.floor(v), i = Math.round((v - f) * 12); return i ? `${f}' ${i}"` : `${f}'`; };

  function drawItem(it) {
    const r = R(it.room); if (!r) return;
    const p = wallPt(r, it.wall, it.off), L = it.w / 12 * S, P = it.p || {};
    const g = el('g', {class: 'fpitem' + (it.id === selItem ? ' sel' : '')});
    g.addEventListener('pointerdown', e => { e.stopPropagation(); selItem = it.id; selRoom = it.room; draw(); panel(); });
    const x2 = p.x + p.dx * L, y2 = p.y + p.dy * L;
    el('line', {x1: p.x, y1: p.y, x2, y2, stroke: '#f7fbff', 'stroke-width': 8}, g);
    if (it.t === 'door') {
      const s = (P.swing || 'in') === 'in' ? 1 : -1, left = (P.hinge || 'Left') === 'Left';
      const hx = left ? p.x : x2, hy = left ? p.y : y2, fx = left ? x2 : p.x, fy = left ? y2 : p.y;
      const ox = hx + p.nx * L * s, oy = hy + p.ny * L * s;
      el('line', {x1: hx, y1: hy, x2: ox, y2: oy, stroke: '#92400e', 'stroke-width': 3}, g);
      const cross = (fx - hx) * (oy - hy) - (fy - hy) * (ox - hx);
      el('path', {d: `M ${ox} ${oy} A ${L} ${L} 0 0 ${cross > 0 ? 0 : 1} ${fx} ${fy}`, fill: 'none', stroke: '#92400e', 'stroke-dasharray': '4 3'}, g);
      el('rect', {class: 'hit', x: Math.min(p.x, x2, ox), y: Math.min(p.y, y2, oy), width: Math.max(10, Math.abs(p.dx) * L + Math.abs(p.nx) * L),
                  height: Math.max(10, Math.abs(p.dy) * L + Math.abs(p.ny) * L), fill: 'transparent'}, g);
    } else if (it.t === 'window' || it.t === 'patio') {
      el('rect', {class: 'hit', x: Math.min(p.x, x2) - (p.dx ? 0 : 5), y: Math.min(p.y, y2) - (p.dy ? 0 : 5), width: p.dx ? L : 10, height: p.dy ? L : 10,
                  fill: '#e0f2fe', stroke: '#0369a1', 'stroke-width': 1.5}, g);
      if (it.t === 'patio') el('line', {x1: (p.x + x2) / 2 - p.nx * 5, y1: (p.y + y2) / 2 - p.ny * 5, x2: (p.x + x2) / 2 + p.nx * 5, y2: (p.y + y2) / 2 + p.ny * 5, stroke: '#0369a1', 'stroke-width': 1.5}, g);
    } else {
      const dep = (it.t === 'cab' ? (+P.depth || 24) / 12 : 2) * S;
      const rx = Math.min(p.x, x2, p.x + p.nx * dep), ry = Math.min(p.y, y2, p.y + p.ny * dep);
      const cab = it.t === 'cab', upper = cab && P.kind === 'Wall';
      el('rect', {class: 'hit', x: rx, y: ry, width: p.dx ? L : dep, height: p.dy ? L : dep, fill: cab ? (upper ? 'transparent' : '#ede9fe') : '#fef3c7',
                  stroke: cab ? '#6d28d9' : '#92400e', 'stroke-width': 1.5, 'stroke-dasharray': upper ? '6 4' : ''}, g);
      if (it.t === 'bypass') {
        el('line', {x1: p.x + p.nx * dep * .35, y1: p.y + p.ny * dep * .35, x2: p.x + p.nx * dep * .35 + p.dx * L * .55, y2: p.y + p.ny * dep * .35 + p.dy * L * .55, stroke: '#92400e', 'stroke-width': 3}, g);
        el('line', {x1: p.x + p.nx * dep * .6 + p.dx * L * .45, y1: p.y + p.ny * dep * .6 + p.dy * L * .45, x2: x2 + p.nx * dep * .6, y2: y2 + p.ny * dep * .6, stroke: '#92400e', 'stroke-width': 3}, g);
      }
      if (it.t === 'bifold') for (let i = 0; i < 4; i++) { const a = i / 4, b = (i + .5) / 4, c = (i + 1) / 4;
        el('polyline', {points: `${p.x + p.dx * L * a},${p.y + p.dy * L * a} ${p.x + p.dx * L * b + p.nx * dep * .7},${p.y + p.dy * L * b + p.ny * dep * .7} ${p.x + p.dx * L * c},${p.y + p.dy * L * c}`,
                        fill: 'none', stroke: '#92400e', 'stroke-width': 1.5}, g); }
      if (cab && P.kind === 'Sink Base') el('rect', {x: rx + (p.dx ? L * .3 : dep * .2), y: ry + (p.dy ? L * .3 : dep * .2), width: p.dx ? L * .4 : dep * .6, height: p.dy ? L * .4 : dep * .6, rx: 5, fill: '#fff', stroke: '#6d28d9'}, g);
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
  svg.addEventListener('pointerdown', e => { if (e.target === svg || e.target.tagName === 'line') { selRoom = null; selItem = null; draw(); panel(); } });

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
    if (selItem) {
      const it = plan.items.find(i => i.id === selItem); if (!it) { selItem = null; return panel(); }
      const r = R(it.room), P = it.p || {}, purl = photoUrl(it);
      if (mode === 'view') {
        const rows = [['Where', `${r.n}, ${({N: 'top', S: 'bottom', E: 'right', W: 'left'})[it.wall]} wall`], ['Width', inch(it.w)]]
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
          <label>Wall</label><select data-g="wall">${['N', 'E', 'S', 'W'].map(w => `<option value="${w}" ${w === it.wall ? 'selected' : ''}>${({N: 'Top', E: 'Right', S: 'Bottom', W: 'Left'})[w]}</option>`).join('')}</select>
          ${f}${purl ? `<img class="fpthumb" style="margin-top:8px" src="${purl}" onerror="this.remove()" alt="">` : ''}
          <button class="btn sm danger" type="button" id="rmItem" style="margin-top:8px">Remove</button>`;
        box.querySelectorAll('[data-g]').forEach(i => i.onchange = () => { const k = i.dataset.g; it[k] = k === 'wall' ? i.value : +i.value; dirty = true; draw(); });
        box.querySelectorAll('[data-k]').forEach(i => i.onchange = () => { it.p = it.p || {}; it.p[i.dataset.k] = i.value; dirty = true; draw(); panel(); });
        $('rmItem').onclick = () => { plan.items = plan.items.filter(x => x !== it); selItem = null; dirty = true; draw(); panel(); };
      }
    } else if (selRoom && mode === 'edit') {
      const r = R(selRoom);
      box.innerHTML = `<h3 style="margin-top:0">${esc(r.n)}</h3><label>Name</label><input id="rn" value="${esc(r.n)}">
        <div class="grid g2"><div><label>Width (ft)</label><input id="rw" type="number" step="0.5" value="${r.w}"></div>
        <div><label>Length (ft)</label><input id="rh" type="number" step="0.5" value="${r.h}"></div></div>
        <div class="muted small" style="margin-top:6px">${plan.items.filter(i => i.room === r.id).length} item(s) on this room's walls.</div>
        <button class="btn sm danger" type="button" id="rmRoom" style="margin-top:8px">Delete room</button>`;
      $('rn').onchange = e => { r.n = e.target.value; dirty = true; draw(); };
      $('rw').onchange = e => { r.w = Math.max(1, +e.target.value); dirty = true; draw(); };
      $('rh').onchange = e => { r.h = Math.max(1, +e.target.value); dirty = true; draw(); };
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
    $('sched').querySelectorAll('tr[data-id]').forEach(tr => tr.onclick = () => { selItem = +tr.dataset.id; selRoom = (plan.items.find(i => i.id === selItem) || {}).room; draw(); panel(); });
  }

  // ---------------- tools
  $('addRoom').onclick = () => {
    const w = +$('rW').value || 10, h = +$('rL').value || 10;
    const maxX = plan.rooms.length ? Math.max(...plan.rooms.map(r => r.x + r.w)) : 0;
    const r = {id: nid++, n: $('rName').value || 'Room', x: maxX, y: 0, w, h};
    plan.rooms.push(r); selRoom = r.id; selItem = null; dirty = true; draw(); panel();
  };
  $('fType').onchange = e => { $('fW').value = {door: 32, window: 36, patio: 72, bypass: 72, bifold: 48, cab: 96}[e.target.value]; };
  $('addFeat').onclick = () => {
    if (!selRoom) { alert('Click a room on the plan first.'); return; }
    const t = $('fType').value;
    const it = {id: nid++, room: selRoom, t, wall: $('fWall').value, off: +$('fOff').value || 0, w: +$('fW').value || 32, p: Object.assign({}, DEFAULTS[t])};
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
  setMode(mode);
})();
