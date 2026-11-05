"""Show every size in inches.

    inches_text("2'10\" x 6'8\" Bi-Fold")       -> '34" x 80" Bi-Fold'
    inches_text("7'-0\" x 6'-8\" Bypass")        -> '84" x 80" Bypass'
    inches_text("2/10 x 6/8 slab")              -> '34" x 80" slab'   (door trade shorthand)
    inches_text("6' x 15' Coolaroo")            -> '72" x 180" Coolaroo'
    inches_text("2'-10 1/2\" door")             -> '34-1/2" door'
Inch-only sizes, fractions like 7/16" and 3-1/2", and words with apostrophes are left as they are.
Doubled inch marks typed on invoices (18'' or 36'") become a plain ".
"""
import re
from functools import lru_cache
from math import gcd

RQ = "’"   # right single quote (typed as a foot mark)
RDQ = "”"  # right double quote (typed as an inch mark)

# feet (whole or decimal), then optional inches (whole, whole-fraction, whole fraction, fraction), then a mark
_FT = re.compile(
    r"(?<![0-9a-wyz_/.'-])(\d{1,3}(?:\.\d+)?)\s*(?:(?<!')['" + RQ + r"](?!['a-wyz])|ft\.?(?=\s*\d|\b))"
    r"(?:\s*-?\s*(\d{1,2}(?:\s*[- ]\s*\d+/\d+)?|\d+/\d+)(?:\s*(?:\"|'|in\.?\b))?)?",
    re.I)
# doubled / mixed inch marks: 36'"  2'-4"'  22'''  18''  1 3/4(right quote)  -> one "
_MARKS = re.compile(r"(\d)\s*(?:'\"|\"'+|\"\"|'''|''|" + RDQ + ")")
# "34\" x 6-8\"" - feet-dash-inches after an x (6-8" can't be plain inches: there's no fraction)
_DASH = re.compile(r"(?<=[xX×])(\s*)([5-9])-(\d{1,2})\"(?![\d/])")
# door shorthand: 2/10 x 6/8, 3/0 X 6/8 (feet/inches on both sides of the x)
_SHORT = re.compile(r"(?<![0-9a-wyzA-WYZ_/.\"-])(\d)/(\d{1,2})(\s*[xX×]\s*)(\d)/(\d{1,2})(?![\w/\"'])")


def fmt(v):
    """34.5 -> '34-1/2"'; 80 -> '80"' (to the nearest 1/16)."""
    whole = int(v)
    frac = round((v - whole) * 16)
    if frac == 16:
        whole, frac = whole + 1, 0
    if not frac:
        return f'{whole}"'
    g = gcd(frac, 16)
    return f'{whole}-{frac // g}/{16 // g}"'


def _num(s):
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s:
        return 0.0
    m = re.match(r"^(\d+)\s*[- ]\s*(\d+)/(\d+)$", s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.match(r"^(\d+)/(\d+)$", s)
    if m:
        return int(m.group(1)) / int(m.group(2)) if int(m.group(2)) else 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _short(m):
    a_ft, a_in, sep, b_ft, b_in = m.groups()
    if int(a_in) > 11 or int(b_in) > 11 or max(int(a_ft), int(b_ft)) < 5:   # a fraction like 1/2 x 3/4
        return m.group(0)
    return f"{fmt(int(a_ft) * 12 + int(a_in))}{sep}{fmt(int(b_ft) * 12 + int(b_in))}"


def _dash(m):
    if int(m.group(3)) > 11:
        return m.group(0)
    return m.group(1) + fmt(int(m.group(2)) * 12 + int(m.group(3)))


def _feet(m):
    ft, inch = float(m.group(1)), m.group(2)
    extra = _num(inch)
    if extra >= 12:                            # "6' 15" isn't feet + inches - leave it alone
        return m.group(0)
    if not inch and ft > 20:                   # 60' on a blind, 350 ft of chain: a typo or a roll length
        return m.group(0)
    return fmt(ft * 12 + extra)


@lru_cache(maxsize=50000)
def _convert(text):
    out = _MARKS.sub(r'\1"', text)
    out = _SHORT.sub(_short, out)
    out = _DASH.sub(_dash, out)
    return _FT.sub(_feet, out)


def inches_text(text):
    """Rewrite feet / feet-inches / door shorthand sizes in free text as inches. Non-text passes through."""
    if not isinstance(text, str) or not text:
        return text
    if not re.search(r"\d\s*(?:'|" + RQ + "|" + RDQ + r"|ft)|\d/\d|\d-\d", text, re.I):
        return text
    return _convert(text)
