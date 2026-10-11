/* Quote editor: live totals, add/remove lines, item search that fills this account's price. */
(function () {
  const D = JSON.parse(document.getElementById('qdata').textContent);
  const body = document.querySelector('#lines tbody');
  const num = v => { const f = parseFloat(String(v || '').replace(/[$,]/g, '')); return isNaN(f) ? 0 : f; };
  const money = v => '$' + v.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  function recalc() {
    let t = 0;
    body.querySelectorAll('tr').forEach(tr => {
      const q = num(tr.querySelector('.qty').value), pIn = tr.querySelector('.price'), p = num(pIn.value);
      const a = q * p; t += a;
      tr.querySelector('.amt').textContent = pIn.value.trim() ? money(a) : 'TBD';
      pIn.classList.toggle('needs', !pIn.value.trim());
    });
    document.getElementById('total').textContent = money(t);
  }
  let timer = null, picks = {};
  function wire(tr) {
    tr.querySelector('.rm').onclick = () => { tr.remove(); recalc(); };
    tr.querySelectorAll('.qty,.price').forEach(i => i.addEventListener('input', recalc));
    const desc = tr.querySelector('.desc'), item = tr.querySelector('input[name=item_no]');
    [desc, item].forEach(inp => {
      inp.setAttribute('list', 'itemlist');
      inp.addEventListener('input', () => {
        const v = inp.value.trim();
        const hit = picks[v];
        if (hit) { fill(tr, hit); return; }
        clearTimeout(timer);
        if (v.length < 2) return;
        timer = setTimeout(async () => {
          const r = await fetch(`${D.api}?q=${encodeURIComponent(v)}&customer=${D.customer || ''}`).then(r => r.json()).catch(() => []);
          const dl = document.getElementById('itemlist'); dl.innerHTML = ''; picks = {};
          r.forEach(it => {
            const label = `${it.item_no ? it.item_no + ' - ' : ''}${it.description}`;
            picks[label] = it;
            const o = document.createElement('option'); o.value = label;
            o.label = it.price != null ? `${money(it.price)} · ${it.source}` : 'no price'; dl.appendChild(o);
          });
        }, 200);
      });
    });
  }
  function fill(tr, it) {
    tr.querySelector('input[name=item_no]').value = it.item_no || '';
    tr.querySelector('.desc').value = it.description || '';
    const sel = tr.querySelector('select[name=category]'); if (it.category) sel.value = it.category;
    if (it.price != null) tr.querySelector('.price').value = it.price.toFixed(2);
    tr.querySelector('input[name=price_source]').value = it.source ? `${it.source} ${it.date || ''}`.trim() : '';
    tr.querySelector('.src').textContent = it.source ? `${it.source} ${it.date || ''}` : 'no price history - type a price';
    recalc();
  }
  body.querySelectorAll('tr').forEach(wire);
  document.getElementById('addLine').onclick = () => {
    const tr = document.getElementById('rowtpl').content.firstElementChild.cloneNode(true);
    body.appendChild(tr); wire(tr); tr.querySelector('.desc').focus(); recalc();
  };
  recalc();
})();
