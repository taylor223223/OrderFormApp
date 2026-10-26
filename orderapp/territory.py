"""Sales territories: which accounts are MINE, so the planners only suggest accounts in my territory.

Each rep's territory is a list of ZIP codes (Settings > Territory). An account is in my territory when:
  - its ZIP is one of my ZIPs (Tempe 85281 / 85288 count only north of the Loop 202 when the map point is
    known - the "partial" ZIPs), or
  - it has no ZIP yet and the company reports bill it to me, or
  - the company reports bill it to me and its ZIP isn't in another rep's territory ("bill to me" option).
Accounts in another rep's ZIPs are never suggested, even if they once bought from me.
"""
import re

from flask import g, has_app_context

from .db import q, set_setting, setting

# Taylor's central Phoenix territory (Phoenix_Territory_Zipcodes.xlsx) - used until Settings has a list
DEFAULT_MY_ZIPS = ("85003 85004 85006 85007 85008 85009 85012 85013 85014 85015 85016 85017 85018 85019 85020 "
                   "85021 85031 85033 85034 85035 85040 85041 85042 85043 85051 85250 85251 85253 85257 85258 "
                   "85301 85303 85339")
DEFAULT_PARTIAL = "85281 85288"       # Tempe: only north of the Loop 202
NORTH_OF_202_LAT = 33.436             # the 202 (Red Mountain Fwy) through Tempe runs about here
NOT_REPS = {"house", "non sales", "no sales rep", ""}


def zips_of(text):
    return [z for z in re.findall(r"\b(\d{5})\b", str(text or ""))]


def me():
    return setting("report_rep") or setting("sales_rep") or ""


def reps():
    """Sales reps from the company reports (plus me), biggest first."""
    out = [r["name"] for r in q("SELECT name FROM co_reps ORDER BY summary_total DESC")
           if (r["name"] or "").strip().lower() not in NOT_REPS and (r["name"] or "")[:1].isalpha()]
    m = me()
    if m and m not in out:
        out.insert(0, m)
    return out


def _key(rep):
    return "territory_zips:" + re.sub(r"[^a-z0-9]+", "_", rep.lower()).strip("_")


def rep_zips(rep):
    v = setting(_key(rep))
    if v is None and rep == me():
        return zips_of(DEFAULT_MY_ZIPS)
    return zips_of(v)


def save_rep_zips(rep, text):
    set_setting(_key(rep), " ".join(dict.fromkeys(zips_of(text))))


def my_zips():
    return set(rep_zips(me()))


def partial_zips():
    v = setting("territory_partial")
    return set(zips_of(DEFAULT_PARTIAL if v is None else v))


def enabled():
    return setting("territory_only") != "0"


def other_zips():
    m = me()
    out = {}
    for r in reps():
        if r == m:
            continue
        for z in rep_zips(r):
            out[z] = r
    return out


def billed_rep_map():
    """{company cust_key: rep} - who the company reports bill each account to (most invoices, last 2 years)."""
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=730)).isoformat()
    best = {}
    for r in q("""SELECT cust_key, rep, COUNT(*) n FROM co_docs WHERE coalesce(rep,'')<>'' AND doc_date>=?
                  GROUP BY cust_key, rep""", (since,)):
        if (r["rep"] or "").lower() in NOT_REPS:
            continue
        if r["cust_key"] not in best or r["n"] > best[r["cust_key"]][0]:
            best[r["cust_key"]] = (r["n"], r["rep"])
    return {k: v[1] for k, v in best.items()}


def _zip5(z):
    m = re.search(r"\d{5}", str(z or ""))
    return m.group(0) if m else ""


def zip_rep_map():
    """{zip: rep} from every rep's territory ZIPs (Settings > Territory); mine wins a ZIP that's listed twice."""
    out = dict(other_zips())
    for z in my_zips():
        out[z] = me()
    return out


def rep_for(c, zmap=None):
    """(rep, why) - the sales rep a customer belongs to, by its ZIP code. Split ZIPs (partial) go by the side of
    the 202 the property is on. ("", why) when no rep has that ZIP yet."""
    z = _zip5(c["zip"] if "zip" in c.keys() else "")
    if not z:
        return "", "no ZIP code on the account"
    zmap = zip_rep_map() if zmap is None else zmap
    if z in partial_zips():
        lat = c["lat"] if "lat" in c.keys() else None
        if lat is None or lat >= NORTH_OF_202_LAT:
            return me(), f"ZIP {z} (north of the 202)" if lat is not None else f"ZIP {z}"
        return zmap.get(z, "") if zmap.get(z) != me() else "", f"ZIP {z} (south of the 202)"
    if z in zmap:
        return zmap[z], f"ZIP {z}"
    return "", f"no rep has ZIP {z} yet"


def classify(c, mine=None, partial=None, others=None, billed=None, include_billed=None):
    """'mine' / 'other' / 'unknown' for a customer row (needs name, zip, lat)."""
    mine = my_zips() if mine is None else mine
    partial = partial_zips() if partial is None else partial
    others = other_zips() if others is None else others
    include_billed = (setting("territory_include_billed") != "0") if include_billed is None else include_billed
    z = _zip5(c["zip"])
    if z in mine:
        return "mine"
    if z in partial:
        lat = c["lat"] if "lat" in c.keys() else None
        if lat is None or lat >= NORTH_OF_202_LAT:
            return "mine"
        return "other"
    rep = None
    if billed is not None:
        from .db import norm_name
        rep = billed.get(norm_name(c["name"]))
    if z and z in others:
        return "other"
    if rep and rep == me() and (include_billed or not z):
        return "mine"
    if not z:
        return "unknown" if not rep else ("mine" if rep == me() else "other")
    return "other"


def my_ids():
    """Customer ids in my territory (cached for the request)."""
    if has_app_context() and "terr_ids" in g:
        return g.terr_ids
    mine, partial, others = my_zips(), partial_zips(), other_zips()
    try:
        billed = billed_rep_map()
    except Exception:   # noqa: BLE001 - no company data
        billed = {}
    ids = {c["id"] for c in q("SELECT id, name, zip, lat FROM customers")
           if classify(c, mine, partial, others, billed) == "mine"}
    if has_app_context():
        g.terr_ids = ids
    return ids


def keep(customer_id):
    """True if this account should show in the planners (territory filter off = everything)."""
    if not enabled():
        return True
    return customer_id in my_ids()


def counts():
    mine, partial, others = my_zips(), partial_zips(), other_zips()
    try:
        billed = billed_rep_map()
    except Exception:   # noqa: BLE001
        billed = {}
    out = {"mine": 0, "other": 0, "unknown": 0}
    for c in q("SELECT id, name, zip, lat FROM customers WHERE coalesce(nullif(acct_type,''),'Property')='Property'"):
        out[classify(c, mine, partial, others, billed)] += 1
    return out


def set_options(only, include_billed, partial):
    set_setting("territory_only", "1" if only else "0")
    set_setting("territory_include_billed", "1" if include_billed else "0")
    set_setting("territory_partial", " ".join(zips_of(partial)))
