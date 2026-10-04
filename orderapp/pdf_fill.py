"""Fill the supplied PDF order forms from logical order data."""
import os
import re
import textwrap
from collections import defaultdict

import pymupdf as fitz

from .catalog import FORMS, match_option
from .paths import resource_path

CHECK_TYPES = ("CheckBox", "RadioButton")
FILL_BLUE = (0.05, 0.25, 0.75)      # what the rep filled in stands out from the printed black form


def template_path(form_key):
    return resource_path(os.path.join("forms", FORMS[form_key]["file"]))


def _clean(name):
    # PyMuPDF sometimes reports duplicate parent names as "Parent [123].Kid"
    return re.sub(r" \[\d+\]", "", name or "")


def _index_widgets(page):
    idx = defaultdict(list)
    for w in page.widgets():
        idx[_clean(w.field_name)].append(w)
    return idx


def _get(idx, ref, want_check):
    """ref is a field name or a (name, type) tuple."""
    if isinstance(ref, tuple):
        name, typ = ref
        return [w for w in idx.get(name, []) if w.field_type_string == typ]
    ws = idx.get(ref, [])
    return [w for w in ws if (w.field_type_string in CHECK_TYPES) == want_check]


def _set_text(idx, ref, value):
    ws = _get(idx, ref, False)
    for w in ws:
        w.field_value = "" if value is None else str(value)
        w.text_color = FILL_BLUE
        w.update()
    return bool(ws)


def _set_check(idx, ref, on=True):
    ws = _get(idx, ref, True)
    for w in ws:
        w.field_value = w.on_state() if on else "Off"
        if on:   # solid blue box with a white check - easy to spot
            w.fill_color = FILL_BLUE
            w.border_color = FILL_BLUE
            w.text_color = (1, 1, 1)
        w.update()
    return bool(ws)


def _wrap(text, chars):
    """Split text over N lines of the given approximate widths."""
    lines, rest = [], " ".join(str(text or "").split())
    for i, width in enumerate(chars):
        if not rest:
            lines.append("")
            continue
        if i == len(chars) - 1:
            lines.append(rest)
            rest = ""
            break
        parts = textwrap.wrap(rest, width=width, break_long_words=True)
        first = parts[0] if parts else ""
        lines.append(first)
        rest = rest[len(first):].strip()
    return lines, rest


def _fit_size(text, max_w, size):
    while size > 5 and fitz.get_text_length(text, fontname="helv", fontsize=size) > max_w:
        size -= 0.5
    return size


def _overlay_text(page, spec, value):
    x, y, w = spec["overlay"]
    txt = str(value)
    size = _fit_size(txt, w, spec.get("size", 9))
    page.insert_text((x, y), txt, fontsize=size, fontname="helv", color=FILL_BLUE)


def _overlay_mark(page, xy):
    x, y = xy
    page.draw_rect(fitz.Rect(x - 1, y - 8.5, x + 8.5, y + 1), color=FILL_BLUE, fill=FILL_BLUE)
    page.insert_text((x + 0.3, y - 0.5), "4", fontsize=8, fontname="zadb", color=(1, 1, 1))


def _reset(page):
    for w in page.widgets():
        t = w.field_type_string
        if t in CHECK_TYPES:
            if w.field_value not in (False, "Off", "", None):
                w.field_value = "Off"
                w.update()
        elif t == "Text":
            if w.field_value:
                w.field_value = ""
                w.update()


def _combined_address(h):
    city_line = " ".join(x for x in [h.get("city", ""), h.get("state", ""), h.get("zip", "")] if x).strip()
    parts = [p for p in [h.get("address", ""), city_line] if p]
    return ", ".join(parts)


def _visible(f, block):
    """Fields that only apply when another answer is picked (e.g. threshold only if pre-hung = Yes)."""
    cond = f.get("show_if")
    if not cond:
        return True
    k, allowed = cond
    v = block.get(k)
    return bool(v) and (match_option(v, [(a, a) for a in allowed]) is not None)


def _place_photo(page, idx, fields, block, ref):
    """App-made forms: show the picked product's photo in the line's photo box."""
    from . import option_photos
    ws = idx.get(ref)
    if not ws:
        return
    for f in fields:
        if f.get("photos") and block.get(f["key"]) and _visible(f, block):
            p = option_photos.find(f["photos"], block[f["key"]])
            if p:
                try:
                    page.insert_image(ws[0].rect, filename=p, keep_proportion=True)
                except Exception:   # noqa: BLE001
                    pass
                return


def check_order(form_key, data):
    """Warnings about combinations we don't carry."""
    warns = []
    for i, b in enumerate(data.get("blocks") or [], 1):
        if form_key in ("vertical_blind", "vertical_blind_2"):
            sw, st, col = (str(b.get(k) or "").lower() for k in ("slat_width", "slat_style", "color"))
            if sw.startswith("2") and "rib" in st and "alab" in col:
                warns.append(f"Line {i}: 2\" ribbed vertical slats only come in White - double-check the color.")
    return warns


def _fill_block(page, idx, fields, block, notes, line_no):
    for f in fields:
        key, kind = f["key"], f["kind"]
        val = block.get(key)
        if not _visible(f, block):
            continue
        if kind == "text":
            if val in (None, ""):
                continue
            if f.get("overlay"):
                _overlay_text(page, f, val)
            elif f.get("pdf"):
                _set_text(idx, f["pdf"], val)
            else:   # app-only field: no box on this paper form
                notes.append(f"Line {line_no} {f['label']}: {val}")
            if f.get("auto_check"):
                _set_check(idx, f["auto_check"], True)
        elif kind == "bool":
            if str(val).lower() in ("1", "true", "yes", "y", "on", "x"):
                _set_check(idx, f["pdf"], True)
        elif kind == "lines":
            if val:
                lines, rest = _wrap(val, f["chars"])
                for ref, ln in zip(f["pdf"], lines):
                    _set_text(idx, ref, ln)
                if rest:
                    notes.append(f"Line {line_no} {f['label']}: ...{rest}")
        elif kind == "choice":
            if val in (None, ""):
                continue
            opt = match_option(val, f["options"])
            if opt is None and f.get("other_option"):
                opt = f["other_option"]
                other_key = f.get("other_field")
                if other_key and not block.get(other_key):
                    other_spec = next((x for x in fields if x["key"] == other_key), None)
                    if other_spec is not None:
                        if other_spec.get("overlay"):
                            _overlay_text(page, other_spec, val)
                        elif other_spec.get("pdf"):
                            _set_text(idx, other_spec["pdf"], val)
            if opt is None:
                notes.append(f"Line {line_no} {f['label']}: {val}")
                continue
            target = dict((o[0], o[1]) for o in f["options"])[opt]
            if f.get("note_always") or target is None:
                notes.append(f"Line {line_no} {f['label']}: {val if target is None else opt}")
            if target is None:
                continue
            if isinstance(target, dict) and "mark" in target:
                _overlay_mark(page, target["mark"])
            elif isinstance(target, list):
                for ref in target:
                    _set_check(idx, ref, True)
            else:
                _set_check(idx, target, True)


def fill_form(form_key, data):
    """Return (pdf_bytes, warnings). data = {header:{}, sales_rep, comments, blocks:[{}]}"""
    pdf, warns = _fill_any(form_key, data)
    return pdf, check_order(form_key, data) + list(warns)


def _fill_any(form_key, data):
    from .catalog import MERGED, choose_variant, translate_for_variant
    if form_key in MERGED:
        variant = choose_variant(form_key, data)
        data = translate_for_variant(form_key, variant, data)
        pdf, warns = _fill(variant, data)
        if variant != MERGED[form_key]["default"]:
            warns = [f"Printed on the {FORMS[variant]['title']} paper form (matches what you ordered)."] + list(warns)
        return pdf, warns
    return _fill(form_key, data)


def _fill(form_key, data):
    spec = FORMS[form_key]
    per_page = len(spec["blocks"])
    blocks = [b for b in (data.get("blocks") or []) if any(str(v).strip() for v in b.values() if v is not None)]
    if not blocks:
        blocks = [{}]
    has_unit_field = any(f["key"] == "unit" for f in spec["blocks"][0])
    header = dict(data.get("header") or {})
    warnings = []
    out = fitz.open()
    pages = [blocks[i:i + per_page] for i in range(0, len(blocks), per_page)]
    for pnum, chunk in enumerate(pages):
        doc = fitz.open(template_path(form_key))
        page = doc[0]
        _reset(page)
        idx = _index_widgets(page)
        # header
        for key, ref in spec["header"].items():
            val = header.get(key, "")
            if key == "address" and spec.get("address_combined"):
                val = _combined_address(header)
            if val:
                _set_text(idx, ref, val)
        if spec.get("sales_rep") and data.get("sales_rep"):
            _set_text(idx, spec["sales_rep"], data["sales_rep"])
        notes = []
        for i, blk in enumerate(chunk):
            line_no = pnum * per_page + i + 1
            bn = []
            _fill_block(page, idx, spec["blocks"][i], blk, bn, line_no)
            if spec.get("app_made"):
                _place_photo(page, idx, spec["blocks"][i], blk, f"L{i}.photo")
            for f in spec["blocks"][i]:   # company forms with a photo box per option group
                if f.get("photos") and f"Photo {f['photos']}.{i}" in idx:
                    _place_photo(page, idx, [f], blk, f"Photo {f['photos']}.{i}")
            lc = next((f for f in spec["blocks"][i] if f["key"] == "line_comments"), None)
            if bn and not spec.get("comments") and lc:
                # no general comments box: this line's extras go in this line's own comments
                txt = "; ".join([x for x in [blk.get("line_comments", "")] if x] +
                                [re.sub(r"^Line \d+ ", "", n) for n in bn])
                lines, rest = _wrap(txt, lc["chars"])
                for ref, ln in zip(lc["pdf"], lines):
                    _set_text(idx, ref, ln)
                if rest:
                    warnings.append(f"Line {line_no} comments cut off: '{rest}'")
            else:
                notes.extend(bn)
            if not has_unit_field and blk.get("unit"):
                notes.insert(0, f"Line {line_no}: Unit {blk['unit']}" +
                             (f" ({blk['room']})" if blk.get("room") and not any(
                                 f['key'] == 'room' for f in spec['blocks'][i]) else ""))
        comment_text = " ".join(x for x in [data.get("comments", "")] if x)
        if len(pages) > 1:
            comment_text = f"Page {pnum + 1} of {len(pages)}. " + comment_text
        all_comments = "; ".join([c for c in [comment_text.strip()] + notes if c])
        if spec.get("comments"):
            lines, rest = _wrap(all_comments, spec["comments"]["chars"])
            for ref, ln in zip(spec["comments"]["pdf"], lines):
                _set_text(idx, ref, ln)
            if rest:
                warnings.append(f"Comments too long for the form; cut off: '{rest}'")
        elif all_comments:
            # forms without a general comments box: use the last block's line comments
            lc = next((f for f in spec["blocks"][len(chunk) - 1] if f["key"] == "line_comments"), None)
            last_used = lc and lc["pdf"][0] in idx and any(w.field_value for w in idx[lc["pdf"][0]])
            if lc and not last_used:
                _set_text(idx, lc["pdf"][0], all_comments)
            else:
                warnings.append("This form has no comments box; not printed: " + all_comments)
        out.insert_pdf(doc)
        doc.close()
    if len(pages) > 1:
        # identical field names on several pages would mirror each other: flatten
        out.bake()
        warnings.append(f"{len(blocks)} line items -> {len(pages)} pages (flattened).")
    pdf = out.tobytes(garbage=3, deflate=True)
    out.close()
    return pdf, warnings


def read_filled_form(pdf_bytes):
    """Reverse-map a filled copy of one of our forms (e.g. a customer emailed one
    back) into (form_key, data). Returns (None, None) if it isn't recognised."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return None, None
    if doc.page_count == 0:
        return None, None
    page = doc[0]
    vals = {}
    names = set()
    for w in page.widgets():
        nm = _clean(w.field_name)
        names.add(nm)
        t = w.field_type_string
        if t in CHECK_TYPES:
            on = w.field_value not in (False, "Off", "", None)
            vals[(nm, "CheckBox")] = vals.get((nm, "CheckBox")) or on
        else:
            vals[(nm, "Text")] = vals.get((nm, "Text")) or w.field_value or ""
    if not names:
        return None, None
    scores = []
    for fk in FORMS:
        tmpl = fitz.open(template_path(fk))
        tnames = {_clean(w.field_name) for w in tmpl[0].widgets()}
        tmpl.close()
        # how much of the filled copy this form explains (older copies have fewer boxes than ours)
        cover = len(names & tnames) / max(len(names), 1)
        scores.append((round(cover, 3), len(names & tnames) / max(len(tnames | names), 1), fk))
    scores.sort(reverse=True)
    if not scores or scores[0][0] < 0.85:
        return None, None
    top = [fk for sc, _, fk in scores if sc >= scores[0][0] - 0.02]
    best = top[0]
    if "prehung" in top or "door" in top:
        # the Pre-Hung form reuses the Door form's field names; tell them apart by title
        best = "prehung" if "PRE-HUNG DOOR FORM" in page.get_text().upper() else ("door" if "door" in top else best)
    spec = FORMS[best]

    def text(ref):
        if isinstance(ref, tuple):
            return vals.get(ref, "")
        return vals.get((ref, "Text"), "")

    def checked(ref):
        if isinstance(ref, tuple):
            return bool(vals.get(ref))
        return bool(vals.get((ref, "CheckBox")))

    data = {"header": {}, "blocks": []}
    for key, ref in spec["header"].items():
        v = text(ref)
        if v:
            data["header"][key] = v
    if spec.get("sales_rep"):
        data["sales_rep"] = text(spec["sales_rep"])
    if spec.get("comments"):
        data["comments"] = " ".join(text(r) for r in spec["comments"]["pdf"] if text(r)).strip()
    for fields in spec["blocks"]:
        blk = {}
        for f in fields:
            if f["kind"] == "text" and f.get("pdf"):
                v = text(f["pdf"])
                if v:
                    blk[f["key"]] = v
            elif f["kind"] == "bool":
                if checked(f["pdf"]):
                    blk[f["key"]] = "Yes"
            elif f["kind"] == "lines":
                v = " ".join(text(r) for r in f["pdf"] if text(r)).strip()
                if v:
                    blk[f["key"]] = v
            elif f["kind"] == "choice":
                for lab, target in f["options"]:
                    refs = target if isinstance(target, list) else [target]
                    if isinstance(target, dict):
                        continue
                    if all(checked(r) for r in refs):
                        blk[f["key"]] = lab
                        break
        if blk:
            data["blocks"].append(blk)
    return best, data


def validate_catalog():
    """Return list of problems: pdf field names in the catalog that don't exist."""
    problems = []
    for fk, spec in FORMS.items():
        doc = fitz.open(template_path(fk))
        idx = _index_widgets(doc[0])

        def chk(ref, check):
            if ref is None:
                return
            if not _get(idx, ref, check):
                problems.append(f"{fk}: missing {'checkbox' if check else 'text'} {ref!r}")
        for ref in spec["header"].values():
            chk(ref, False)
        if spec.get("sales_rep"):
            chk(spec["sales_rep"], False)
        if spec.get("comments"):
            for r in spec["comments"]["pdf"]:
                chk(r, False)
        for fields in spec["blocks"]:
            for f in fields:
                if f.get("extra"):
                    continue
                if f["kind"] == "text" and not f.get("overlay"):
                    chk(f["pdf"], False)
                    if f.get("auto_check"):
                        chk(f["auto_check"], True)
                elif f["kind"] == "bool":
                    chk(f["pdf"], True)
                elif f["kind"] == "lines":
                    for r in f["pdf"]:
                        chk(r, False)
                elif f["kind"] == "choice":
                    for _, t in f["options"]:
                        if isinstance(t, dict) or t is None:
                            continue
                        for r in (t if isinstance(t, list) else [t]):
                            chk(r, True)
        doc.close()
    return problems
