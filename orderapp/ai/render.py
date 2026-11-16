"""Showing the assistant's answers nicely (v2.37.0): light formatting (bold, lists, headings, tables) and charts.

md(text)        -> safe HTML from the assistant's text (it writes simple markdown).
chart_svg(spec) -> an SVG bar / line / pie chart drawn right in the page (works offline - no web libraries).
"""
import html
import math
import re

from markupsafe import Markup

PALETTE = ["#2563eb", "#f97316", "#16a34a", "#9333ea", "#dc2626", "#0891b2", "#ca8a04", "#db2777"]


def _inline(s):
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__(.+?)__", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def md(text):
    """Markdown-ish text -> HTML. Everything is escaped first, so nothing in the text can inject HTML."""
    lines = html.escape(text or "").split("\n")
    out, i, para = [], 0, []

    def flush():
        if para:
            out.append("<p style='margin:4px 0'>" + "<br>".join(_inline(p) for p in para) + "</p>")
            para.clear()
    while i < len(lines):
        ln = lines[i].rstrip()
        st = ln.strip()
        if st.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            flush()
            head = [c.strip() for c in st.strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            num = re.compile(r"^[\s$€(]*-?[\d,]+(\.\d+)?%?\)?$")
            t = ["<div class='tablewrap'><table class='small' style='margin:4px 0'><tr>" +
                 "".join(f"<th>{_inline(h)}</th>" for h in head) + "</tr>"]
            for r in rows:
                t.append("<tr>" + "".join(f"<td style='{'text-align:right' if num.match(c or 'x') else ''}'>{_inline(c)}</td>"
                                          for c in r) + "</tr>")
            out.append("".join(t) + "</table></div>")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", st)
        if m:
            flush()
            size = {1: 16, 2: 15, 3: 14, 4: 13}[len(m.group(1))]
            out.append(f"<div style='font-weight:700;font-size:{size}px;margin:8px 0 2px'>{_inline(m.group(2))}</div>")
            i += 1
            continue
        if re.match(r"^([-*•]|\d+[.)])\s+", st):
            flush()
            ordered = bool(re.match(r"^\d", st))
            items = []
            while i < len(lines) and re.match(r"^\s*([-*•]|\d+[.)])\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*•]|\d+[.)])\s+", "", lines[i]))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag} style='margin:3px 0 3px 20px;padding:0'>" +
                       "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        if not st:
            flush()
        else:
            para.append(ln)
        i += 1
    flush()
    return Markup("".join(out))


def _fmt(v, money=False):
    if v is None:
        return ""
    if money:
        return f"${v:,.0f}" if abs(v) >= 100 else f"${v:,.2f}"
    return f"{v:,.0f}" if float(v).is_integer() or abs(v) >= 100 else f"{v:,.1f}"


def _nice_max(v):
    if v <= 0:
        return 1
    p = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * p:
            return m * p
    return 10 * p


def chart_svg(spec):
    """spec: {type: bar|line|pie, title, labels: [...], series: [{name, values: [...]}], money: bool}."""
    try:
        return Markup(_chart(spec or {}))
    except Exception as e:  # noqa: BLE001 - a bad chart never breaks the page
        return Markup(f"<div class='small muted'>(couldn't draw that chart: {html.escape(str(e))})</div>")


def _chart(spec):
    kind = (spec.get("type") or "bar").lower()
    title = html.escape(str(spec.get("title") or ""))
    labels = [html.escape(str(x)) for x in spec.get("labels") or []]
    series = spec.get("series") or ([{"name": "", "values": spec.get("values") or []}] if spec.get("values") else [])
    money = bool(spec.get("money"))
    W, H = 640, 300
    parts = [f"<svg viewBox='0 0 {W} {H}' width='100%' style='max-width:{W}px;background:#fff;border:1px solid #e5e7eb;"
             f"border-radius:8px' role='img' aria-label='{title}' xmlns='http://www.w3.org/2000/svg' "
             f"font-family='Arial, sans-serif'>"]
    if title:
        parts.append(f"<text x='14' y='22' font-size='14' font-weight='700' fill='#111827'>{title}</text>")
    if kind == "pie":
        vals = [float(v or 0) for v in (series[0]["values"] if series else [])]
        tot = sum(v for v in vals if v > 0) or 1
        cx, cy, r, a0 = 150, 165, 105, -math.pi / 2
        for i, v in enumerate(vals):
            if v <= 0:
                continue
            a1 = a0 + 2 * math.pi * v / tot
            large = 1 if a1 - a0 > math.pi else 0
            x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
            x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
            col = PALETTE[i % len(PALETTE)]
            if v >= tot:
                parts.append(f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='{col}'/>")
            else:
                parts.append(f"<path d='M{cx},{cy} L{x0:.1f},{y0:.1f} A{r},{r} 0 {large} 1 {x1:.1f},{y1:.1f} Z' "
                             f"fill='{col}' stroke='#fff' stroke-width='2'/>")
            a0 = a1
        for i, (lab, v) in enumerate(zip(labels, vals)):
            y = 60 + i * 22
            if y > H - 10:
                break
            parts.append(f"<rect x='290' y='{y - 11}' width='12' height='12' rx='2' fill='{PALETTE[i % len(PALETTE)]}'/>"
                         f"<text x='308' y='{y}' font-size='12' fill='#374151'>{lab} - {_fmt(v, money)} "
                         f"({v / tot * 100:.0f}%)</text>")
        parts.append("</svg>")
        return "".join(parts)
    n = max(len(labels), max((len(s.get("values") or []) for s in series), default=0))
    allv = [float(v) for s in series for v in (s.get("values") or []) if v is not None]
    top = _nice_max(max(allv + [0]))
    left, right, tp, bot = 64, 16, 40 if not any(s.get("name") for s in series) else 56, 46
    pw, ph = W - left - right, H - tp - bot
    if any(s.get("name") for s in series):     # legend
        x = left
        for i, s in enumerate(series):
            nm = html.escape(str(s.get("name") or ""))
            parts.append(f"<rect x='{x}' y='32' width='12' height='12' rx='2' fill='{PALETTE[i % len(PALETTE)]}'/>"
                         f"<text x='{x + 16}' y='42' font-size='12' fill='#374151'>{nm}</text>")
            x += 30 + 7 * len(nm)
    for k in range(5):                       # gridlines + axis numbers
        v = top * k / 4
        y = tp + ph - ph * k / 4
        parts.append(f"<line x1='{left}' x2='{W - right}' y1='{y:.1f}' y2='{y:.1f}' stroke='#e5e7eb'/>"
                     f"<text x='{left - 6}' y='{y + 4:.1f}' font-size='11' fill='#6b7280' text-anchor='end'>"
                     f"{_fmt(v, money)}</text>")
    step = pw / max(n, 1)
    every = max(1, math.ceil(n / 14))
    for i, lab in enumerate(labels[:n]):
        if i % every:
            continue
        x = left + step * (i + 0.5)
        parts.append(f"<text x='{x:.1f}' y='{H - bot + 16}' font-size='11' fill='#374151' text-anchor='middle'>"
                     f"{lab[:14]}</text>")
    if kind == "line":
        for si, s in enumerate(series):
            col = PALETTE[si % len(PALETTE)]
            pts = [(left + step * (i + 0.5), tp + ph - ph * float(v) / top) for i, v in enumerate(s.get("values") or [])
                   if v is not None]
            if pts:
                parts.append(f"<polyline fill='none' stroke='{col}' stroke-width='2.5' points='"
                             + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "'/>")
                parts += [f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3' fill='{col}'/>" for x, y in pts]
    else:
        k = max(len(series), 1)
        bw = step * 0.72 / k
        for si, s in enumerate(series):
            col = PALETTE[si % len(PALETTE)]
            for i, v in enumerate((s.get("values") or [])[:n]):
                if v is None:
                    continue
                h = ph * float(v) / top
                x = left + step * i + step * 0.14 + bw * si
                parts.append(f"<rect x='{x:.1f}' y='{tp + ph - h:.1f}' width='{max(bw - 1, 1):.1f}' height='{h:.1f}' "
                             f"rx='2' fill='{col}'><title>{labels[i] if i < len(labels) else ''}: "
                             f"{_fmt(float(v), money)}</title></rect>")
                if k == 1 and n <= 12:
                    parts.append(f"<text x='{x + bw / 2:.1f}' y='{tp + ph - h - 4:.1f}' font-size='10' fill='#374151' "
                                 f"text-anchor='middle'>{_fmt(float(v), money)}</text>")
    parts.append(f"<line x1='{left}' x2='{W - right}' y1='{tp + ph}' y2='{tp + ph}' stroke='#9ca3af'/></svg>")
    return "".join(parts)
