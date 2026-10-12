"""Demo copy: fills an empty database with made-up data so every page has something to show.
Every property, person, phone, email and dollar amount here is invented (".example" emails, 555 numbers)."""
import json
import os
import random
from datetime import date, datetime, timedelta

from . import floorplan as FP
from .catalog import FORMS
from .db import add_status, connect, dumps, init_db, now, q, set_setting, x
from .security import create_user

DEMO_USER, DEMO_PASS = "demo", "demo1234"

# name, mgmt, address, city, zip, lat, lon, status, units, phone
PROPS = [
    ("Saguaro Ridge Apartments", "Desert Sky Living", "4410 N Demo Canyon Rd", "Phoenix", "85018", 33.502, -111.985, "Active", 220),
    ("Palo Verde Commons", "Copper State Residential", "1200 E Sample Pkwy", "Tempe", "85281", 33.425, -111.920, "Active", 312),
    ("Camelback Lofts", "Desert Sky Living", "7000 E Example Ave", "Scottsdale", "85251", 33.502, -111.928, "Active", 140),
    ("Mesquite Flats", "Sonoran Property Group", "2550 W Mockingbird Ln", "Phoenix", "85021", 33.560, -112.110, "Active", 186),
    ("Ironwood Terrace", "Copper State Residential", "880 S Placeholder Dr", "Mesa", "85210", 33.400, -111.840, "Active", 264),
    ("Cholla Park Residences", "Sonoran Property Group", "3301 N Fictional Blvd", "Phoenix", "85012", 33.484, -112.068, "Active", 198),
    ("Agave Pointe", "Red Rock Communities", "15 E Testing St", "Chandler", "85225", 33.305, -111.840, "Active", 240),
    ("Ocotillo Gardens", "Red Rock Communities", "4620 E Sampleview Rd", "Phoenix", "85044", 33.333, -111.985, "Prospect", 300),
    ("Desert Bloom Villas", "Sunbelt Living (demo)", "9100 N Madeup Way", "Peoria", "85345", 33.570, -112.240, "Prospect", 156),
    ("Sonoran Heights", "Sunbelt Living (demo)", "702 W Imaginary Ave", "Glendale", "85301", 33.538, -112.190, "Lead", 210),
    ("Paloma Row", "Desert Sky Living", "51 N Pretend Rd", "Gilbert", "85234", 33.355, -111.770, "Lead", 128),
    ("Canyon Vista Apartments", "Sonoran Property Group", "6000 S Example Rd", "Tempe", "85283", 33.375, -111.930, "Inactive", 176),
]
FIRST = ["Dana", "Luis", "Maria", "Kevin", "Ashley", "Marcus", "Priya", "Tom", "Rosa", "Jake", "Nicole", "Andre",
         "Brenda", "Carlos", "Erin", "Victor", "Hannah", "Omar", "Lauren", "Derek"]
LAST = ["Whitfield", "Ortega", "Delgado", "Brooks", "Nguyen", "Bell", "Shah", "Keller", "Ramirez", "Turner",
        "Lopez", "Price", "Fowler", "Mendez", "Walsh", "Romero", "Park", "Haddad", "Gibbs", "Lane"]

# items for sales history: (category, item #, description, unit price low, high, qty low, high)
ITEMS = [
    ("Blinds & Shades", "VB-35-ALB", "Vertical blind 3-1/2\" smooth alabaster", 85, 160, 1, 6),
    ("Blinds & Shades", "FW-2-WHT", "2\" faux wood blind white", 38, 95, 2, 12),
    ("Blinds & Shades", "MP-1-WHT", "1\" Metal Plus mini blind white", 14, 34, 4, 24),
    ("Blinds & Shades", "RS-CLT", "Roller shade clutch, light filtering", 70, 180, 1, 4),
    ("Doors", "D-3080-HC6", "30x80 hollow core 6 panel primecoat", 52, 78, 1, 8),
    ("Doors", "D-2880-SH1", "28x80 1 panel shaker HC primecoat", 64, 92, 1, 6),
    ("Doors", "PH-3068-STL", "36x80 pre-hung 6 panel steel entry door", 240, 420, 1, 3),
    ("Closet & Wardrobe Doors", "BP-6080-FL", "60x80 bi-pass closet door set flat", 95, 160, 1, 4),
    ("Closet & Wardrobe Doors", "MB-7280-SN", "72x80 mirrored bypass satin nickel", 210, 340, 1, 3),
    ("Closet & Wardrobe Doors", "BF-3680", "36x80 bi-fold louver primecoat", 60, 110, 1, 4),
    ("Screens", "WS-BRZ", "Window screen bronze frame charcoal mesh", 14, 32, 4, 30),
    ("Screens", "SD-600-WHT", "Patio screen door 600 series white", 85, 140, 1, 6),
    ("Screens", "SS-90-BLK", "Sunscreen Suntex 90 black", 40, 85, 2, 18),
    ("Baseboard & Casing", "B-387-3", "1\" eased edge craftsman base 3-1/2\" (per ft)", 0.9, 1.6, 120, 900),
    ("Baseboard & Casing", "C-103MUL", "#103 casing MDF 2-1/4\" (per ft)", 0.7, 1.3, 80, 600),
    ("Door Hardware", "HW-SOMA-PS", "Soma lever passage satin nickel", 14, 24, 2, 20),
    ("Door Hardware", "HW-TIB-EN", "Tiburon lever entry matte black", 26, 44, 1, 10),
    ("Bath Hardware", "BH-WF-SET", "Waterfront towel bar / ring / paper holder set", 28, 52, 1, 10),
    ("Cabinets", "CB-SH-B30", "Shaker 30\" base cabinet white", 160, 290, 1, 6),
    ("Cabinets", "VN-36-SNK", "36\" vanity double box with sink", 220, 380, 1, 4),
    ("Countertops", "CT-THN-96", "Thinscape countertop 96\" run", 380, 720, 1, 3),
    ("Countertops", "CT-LAM-8", "Laminate top 8' with backsplash", 160, 320, 1, 4),
    ("Shower Doors", "SH-BYP-60", "60\" bypass tub door clear, chrome", 260, 480, 1, 2),
    ("Mirror Frames", "MM-3648", "Mirror Mate frame kit 36x48", 85, 150, 1, 6),
]
# product mix per property (index into ITEMS weights) - gives each account its own "personality"
MIX = {0: [0, 1, 4, 10, 13, 15], 1: [0, 2, 4, 7, 11, 13, 14], 2: [1, 3, 8, 18, 20, 23], 3: [0, 4, 5, 10, 12, 15],
       4: [2, 4, 6, 7, 9, 11, 16], 5: [0, 1, 4, 13, 17, 19, 21], 6: [0, 4, 10, 13, 14, 15, 16, 22],
       11: [0, 4, 10]}

# floor plans (feet); items are in inches - same format the Floor Plan page saves
PLAN_1BR = {"rooms": [
    {"id": 1, "n": "Living Room", "x": 0, "y": 0, "w": 15, "h": 13},
    {"id": 2, "n": "Kitchen", "x": 15, "y": 0, "w": 10, "h": 9, "open": ["W"]},
    {"id": 3, "n": "Bedroom", "x": 15, "y": 9, "w": 12, "h": 11},
    {"id": 4, "n": "Bath", "x": 6, "y": 13, "w": 9, "h": 7, "bb": False}],
    "items": [
    {"id": 10, "room": 1, "t": "patio", "wall": "N", "off": 4, "w": 72, "p": {"height": "80", "covering": "Vertical Blind", "color": "Alabaster"}},
    {"id": 11, "room": 1, "t": "door", "wall": "W", "off": 8, "w": 36, "p": {"location": "Exterior", "hinge": "Left", "swing": "in", "height": "80", "style": "6 Panel", "material": "Metal"}},
    {"id": 12, "room": 2, "t": "cab", "wall": "N", "off": 1, "w": 96, "p": {"kind": "Sink Base", "countertop": "Thinscape", "style": "Shaker"}},
    {"id": 13, "room": 3, "t": "window", "wall": "E", "off": 3, "w": 60, "p": {"height": "48", "covering": "Horizontal Blind", "slat": "2\"", "color": "White", "screen": "Yes"}},
    {"id": 14, "room": 3, "t": "door", "wall": "W", "off": 6, "w": 30, "p": {"hinge": "Right", "swing": "in", "height": "80", "style": "1 Panel Shaker", "core": "Hollow"}},
    {"id": 15, "room": 3, "t": "bypass", "wall": "S", "off": 3, "w": 72, "p": {"height": "80", "style": "Flat (smooth)"}},
    {"id": 16, "room": 4, "t": "door", "wall": "N", "off": 2, "w": 28, "p": {"hinge": "Left", "swing": "in", "height": "80", "style": "1 Panel Shaker"}}]}
PLAN_2BR = {"rooms": [
    {"id": 1, "n": "Living Room", "x": 0, "y": 0, "w": 16, "h": 14},
    {"id": 2, "n": "Kitchen", "x": 16, "y": 0, "w": 11, "h": 10, "open": ["W"]},
    {"id": 3, "n": "Hallway", "x": 16, "y": 10, "w": 4, "h": 12, "kind": "hall", "open": ["N"]},
    {"id": 4, "n": "Bedroom 1", "x": 20, "y": 10, "w": 12, "h": 12},
    {"id": 5, "n": "Bedroom 2", "x": 6, "y": 14, "w": 10, "h": 10, "cut": {"NE": 4}}],
    "items": [
    {"id": 10, "room": 1, "t": "patio", "wall": "N", "off": 4, "w": 72, "p": {"height": "80", "covering": "Vertical Blind"}},
    {"id": 11, "room": 1, "t": "door", "wall": "W", "off": 9, "w": 36, "p": {"location": "Exterior", "hinge": "Left", "swing": "in", "height": "80", "style": "6 Panel", "material": "Metal"}},
    {"id": 12, "room": 2, "t": "cab", "wall": "N", "off": 1, "w": 120, "p": {"kind": "Sink Base", "countertop": "Laminate"}},
    {"id": 13, "room": 2, "t": "window", "wall": "E", "off": 3, "w": 36, "p": {"height": "36", "covering": "Horizontal Blind", "slat": "1\""}},
    {"id": 14, "room": 4, "t": "door", "wall": "W", "off": 2, "w": 30, "p": {"hinge": "Right", "swing": "in", "height": "80", "style": "2 Panel"}},
    {"id": 15, "room": 4, "t": "window", "wall": "E", "off": 3, "w": 60, "p": {"height": "48", "covering": "Horizontal Blind", "slat": "2\"", "color": "White"}},
    {"id": 16, "room": 4, "t": "bypass", "wall": "S", "off": 3, "w": 72, "p": {"height": "80", "style": "Flat (smooth)"}},
    {"id": 17, "room": 5, "t": "door", "wall": "NE", "off": 1.5, "w": 30, "p": {"hinge": "Left", "swing": "in", "height": "80", "style": "Shaker"}},
    {"id": 18, "room": 5, "t": "window", "wall": "S", "off": 3, "w": 48, "p": {"height": "48", "covering": "Roller Shade"}},
    {"id": 19, "room": 5, "t": "bifold", "wall": "W", "off": 2, "w": 48, "p": {"height": "80", "style": "2 Panel"}}]}


def _phone(r, area="602"):
    return f"{area}-555-{r.randint(100, 199):04d}"


def seed(db_path):
    """Fill an EMPTY database with demo data. Needs an app context (uses q/x)."""
    r = random.Random(20261011)
    today = date.today()
    init_db(db_path)
    if q("SELECT 1 FROM customers LIMIT 1", one=True):
        return False
    from .db import get_db
    con = get_db()
    con.execute("PRAGMA synchronous=OFF")      # thousands of small writes: don't wait on the disk for each one
    try:
        _seed(r, today, db_path)
    finally:
        con.execute("PRAGMA synchronous=FULL")
    return True


def _seed(r, today, db_path):
    if not q("SELECT 1 FROM users LIMIT 1", one=True):
        create_user(DEMO_USER, DEMO_PASS)
    for k, v in {"demo": "1", "email_provider": "eml", "email_auto_minutes": "0", "sales_rep": "Demo Rep",
                 "send_mode": "review", "commission_rate": "3", "monthly_goal": "42000",
                 "commission_basis": "sales", "output_dir": os.path.join(os.path.dirname(os.path.abspath(db_path)), "output"),
                 "route_start_address": "5325 S. Kyrene Rd, Suite 103, Tempe, AZ 85283"
                 }.items():
        set_setting(k, v)
    ts = now()
    cids = []
    for i, (name, mgmt, addr, city, zp, lat, lon, status, nunits) in enumerate(PROPS):
        area = "480" if city in ("Tempe", "Scottsdale", "Mesa", "Chandler", "Gilbert") else "602"
        slug = name.split()[0].lower()
        cid = x("""INSERT INTO customers(name, acct, address, city, state, zip, mgmt, phone, email, notes, created,
                   updated, status, tags, lat, lon, geo_address, owner) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (name, str(90001 + i), addr, city, "AZ", zp, mgmt, _phone(r, area), f"office@{slug}.example",
                 f"{nunits} units. Made-up demo property.", ts, ts, status, "demo", lat, lon,
                 f"{addr}, {city}, AZ {zp}", DEMO_USER))
        cids.append(cid)
        roles = ("Property Manager", "Maintenance Supervisor") + (("Assistant Manager",) if i < 4 else ())
        for role, fn, ln in zip(roles, r.sample(FIRST, len(roles)), r.sample(LAST, len(roles))):
            x("INSERT INTO contacts(customer_id, name, role, email, phone) VALUES (?,?,?,?,?)",
              (cid, f"{fn} {ln}", role, f"{fn.lower()}@{slug}.example", _phone(r, area)))
    # floorplans + units + floor plan drawings (measurements come from the drawings)
    for i in range(6):
        cid = cids[i]
        for fp_name, beds, baths, sqft, plan in (("A1", "1", "1", "720", PLAN_1BR), ("B2", "2", "2", "1050", PLAN_2BR)):
            fid = x("INSERT INTO floorplans(customer_id, name, beds, baths, sqft, notes) VALUES (?,?,?,?,?,?)",
                    (cid, fp_name, beds, baths, sqft, "Demo floorplan"))
            FP.save_plan(fid, json.loads(json.dumps(plan)))
            base = 100 if fp_name == "A1" else 200
            for u in range(1, 13):
                x("INSERT INTO units(customer_id, unit_number, building, floorplan_id) VALUES (?,?,?,?)",
                  (cid, str(base + u), str((u - 1) // 6 + 1), fid))
        # a few unit-specific measurements (verified on a walk)
        for unit_no, prod, room, data in (("104", "vertical_blind", "Living Room", {"width": "71 1/2", "height": "80", "color": "Alabaster"}),
                                          ("207", "interior_door", "Bedroom 2", {"width": "30", "height": "80", "swing": "Left Hand"}),
                                          ("211", "window_screen", "Bedroom 1", {"width": "35 1/2", "height": "47 3/4", "frame_finish": "Bronze"})):
            u = q("SELECT id FROM units WHERE customer_id=? AND unit_number=?", (cid, unit_no), one=True)
            if u:
                x("""INSERT INTO measurements(customer_id, unit_id, product, room, data, verified, notes, updated)
                     VALUES (?,?,?,?,?,1,?,?)""", (cid, u["id"], prod, room, dumps(data), "Measured on walk", ts))
    # orders across statuses (order tracking, history, dashboard)
    forms = [("horizontal_blind", lambda u: {"qty": "2", "unit": u, "style": "Faux Wood", "room": "Bedroom", "color": "White", "width": "35 1/2", "height": "47 3/4"}),
             ("vertical_blind", lambda u: {"qty": "1", "room": "Living Room", "width": "71 1/2", "height": "80", "color": "Alabaster", "slat_width": '3-1/2"', "slat_style": "Smooth", "valance": "Standard Valance"}),
             ("door", lambda u: {"qty": "2", "style": "5 Panel", "material": "Wood", "core": "Hollow Core", "door_location": "Interior", "swing": "Left Hand", "width": "30", "height": "80", "finish": "Primecoat"}),
             ("window_screen", lambda u: {"qty": "6", "width": "35 1/2", "height": "47 3/4", "frame_finish": "Bronze", "screen_color": "Charcoal", "screen_type": "Bug Screen"}),
             ("trim", lambda u: {"unit": u, "room": "Whole unit", "trim_type": "Baseboard", "length": "168"}),
             ("closet_bifold", lambda u: {"qty": "1", "unit": u, "room": "Bedroom", "closet_type": "Mirrored Bypass Door", "frame_finish": "Satin Nickel", "width": "72", "height": "80"})]
    statuses = ["Draft", "Sent", "Confirmed", "Backordered", "Shipped", "Delivered", "Completed", "Completed",
                "Sent", "Confirmed", "Delivered", "Completed", "Draft", "Shipped", "Installed", "Completed"]
    for n, st in enumerate(statuses):
        ci = n % 7
        cid = cids[ci]
        c = q("SELECT * FROM customers WHERE id=?", (cid,), one=True)
        fk, blk = forms[n % len(forms)]
        od = today - timedelta(days=3 + n * 4)
        unit = str(100 + r.randint(1, 12) + (100 if n % 2 else 0))
        data = {"header": {"name": c["name"], "acct": c["acct"], "address": c["address"], "city": c["city"],
                           "state": "AZ", "zip": c["zip"], "mgmt": c["mgmt"], "phone": c["phone"],
                           "po": f"PO-{4100 + n}", "date": od.strftime("%m/%d/%Y")},
                "blocks": [blk(unit)]}
        oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, vendor_ref,
                   due_date, notes, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, fk, FORMS[fk]["title"], f"PO-{4100 + n}", od.isoformat(), st,
                 dumps(data), "manual", f"SO-{58000 + n}" if st != "Draft" else "",
                 (od + timedelta(days=10)).isoformat(), "Demo order", ts, ts))
        add_status(oid, "Draft", "Created")
        if st != "Draft":
            add_status(oid, st, "Demo status")
    # CRM: activities, tasks, deals
    kinds = ["Call", "Site visit", "Email", "Drop-in", "Text", "Meeting", "Voicemail"]
    topics = ["Vertical blinds", "Doors", "Turn schedule", "Pricing", "Window screens", "Baseboards", "Samples"]
    for n in range(36):
        ci = r.randrange(len(cids))
        when = datetime.combine(today - timedelta(days=r.randint(0, 40)), datetime.min.time()) + \
            timedelta(hours=r.randint(8, 16), minutes=r.choice([0, 15, 30, 45]))
        k = q("SELECT name FROM contacts WHERE customer_id=? ORDER BY random() LIMIT 1", (cids[ci],), one=True)
        t = r.sample(topics, 2)
        x("""INSERT INTO activities(customer_id, contact_name, kind, occurred_at, subject, topics, notes, outcome,
             owner, created) VALUES (?,?,?,?,?,?,?,?,?,?)""",
          (cids[ci], k["name"] if k else "", r.choice(kinds), when.strftime("%Y-%m-%d %H:%M"),
           f"{t[0]} - {r.choice(['follow up', 'quote', 'turns next month', 'samples drop', 'check-in'])}",
           ", ".join(t), "Demo note: talked through upcoming turns and pricing.", r.choice(["", "Quote requested", "Order coming", "No need right now"]),
           DEMO_USER, ts))
    for n, (title, days, done) in enumerate((("Send vertical blind quote", -2, 0), ("Drop off door samples", 0, 0),
                                             ("Call about 12 unit renovation", 1, 0), ("Confirm PO for screens", 3, 0),
                                             ("Measure B2 floorplan", 5, 0), ("Follow up on Thinscape tops", 8, 0),
                                             ("Check backordered doors", -5, 1), ("Thank-you call", -10, 1))):
        x("INSERT INTO tasks(customer_id, title, due_date, done, done_at, owner, created) VALUES (?,?,?,?,?,?,?)",
          (cids[n % 9], title, (today + timedelta(days=days)).isoformat(), done, ts if done else None, DEMO_USER, ts))
    for n, (name, prod, val, stage) in enumerate((("Unit turn blinds 2027", "Vertical blinds", 18500, "Quote sent"),
                                                  ("Interior door package", "Doors", 9800, "Negotiating"),
                                                  ("Screens - whole property", "Window screens", 6400, "Contacted"),
                                                  ("Kitchen refresh pilot", "Cabinets", 24000, "Lead"),
                                                  ("Mirrored closet doors", "Bi-pass doors", 7200, "Won"),
                                                  ("Baseboard replacement", "Baseboards", 11300, "Quote sent"),
                                                  ("Sunscreens south side", "Window screens", 4800, "Lost"),
                                                  ("New property onboarding", "Vendor setup", 15000, "Contacted"))):
        x("""INSERT INTO deals(customer_id, name, product, value, stage, close_date, notes, owner, created, updated)
             VALUES (?,?,?,?,?,?,?,?,?,?)""", (cids[(n * 2) % len(cids)], name, prod, val, stage,
                                               (today + timedelta(days=15 + n * 9)).isoformat(), "Demo deal", DEMO_USER, ts, ts))
    # routes: today + next workday, and a trip log for the last two weeks
    nxt = today + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    for dd, picks in ((today, [0, 3, 5, 1]), (nxt, [6, 4, 2])):
        for pos, ci in enumerate(picks):
            p = PROPS[ci]
            x("""INSERT INTO route_stops(day, position, customer_id, label, address, lat, lon, purpose, visit_min, status,
                 source, created, products) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (dd.isoformat(), pos, cids[ci], p[0], f"{p[2]}, {p[3]}, AZ {p[4]}", p[5], p[6],
               r.choice(["Measure", "Drop samples", "Check-in", "Quote walk"]), 20, "Planned", "demo", ts,
               r.choice(["Blinds", "Doors", "Window screens", "Baseboards"])))
    for n in range(1, 15):
        dd = today - timedelta(days=n)
        if dd.weekday() >= 5:
            continue
        stops = r.randint(3, 7)
        x("""INSERT INTO trip_log(day, stops, visited, skipped, miles, drive_s, estimated, start_address, places, updated)
             VALUES (?,?,?,?,?,?,1,?,?,?)""", (dd.isoformat(), stops, stops - (n % 3 == 0), int(n % 3 == 0),
                                              round(r.uniform(28, 74), 1), r.randint(3000, 7200),
                                              "5325 S. Kyrene Rd, Tempe", ", ".join(PROPS[k][0] for k in r.sample(range(8), 3)), ts))
    # account holds (one linked to a customer, one not in the app)
    x("""INSERT INTO account_holds(name, name_key, customer_id, balance, rep, terms, status, notes, since, updated)
         VALUES (?,?,?,?,?,?,?,?,?,?)""", ("Canyon Vista Apartments", "canyonvista", cids[11], 4318.55, "DR", "Net 30",
                                           "hold", "Demo: 75 days past due", (today - timedelta(days=21)).isoformat(), ts))
    x("""INSERT INTO account_holds(name, name_key, customer_id, balance, rep, terms, status, notes, since, updated)
         VALUES (?,?,?,?,?,?,?,?,?,?)""", ("Rio Salado Townhomes (demo)", "riosaladotownhomes", None, 1290.00, "DR",
                                           "Net 30", "hold", "Demo: not a customer in the app yet",
                                           (today - timedelta(days=9)).isoformat(), ts))
    _seed_sales(r, cids, today)
    _seed_review(cids)
    _seed_quotes_prices_deliveries(cids, today)


def _seed_sales(r, cids, today):
    """~22 months of invoices: seasonality, growth, and each account's own product mix."""
    from .sales import import_rows
    rows = []
    start = date(today.year - 1, 1, 1)
    inv = 70000
    d = start
    season = [0.8, 0.85, 1.0, 1.05, 1.15, 1.25, 1.2, 1.1, 1.0, 0.95, 0.85, 0.7]   # summer turn season
    while d <= today:
        if d.weekday() < 5:
            growth = 1.12 if d.year == today.year else 1.0
            n_inv = r.choices([0, 1, 2, 3], weights=[2, 4, 3, 1])[0]
            for _ in range(n_inv):
                if r.random() > season[d.month - 1] * growth * 0.85:
                    continue
                ci = r.choices(list(MIX.keys()), weights=[9, 12, 5, 7, 8, 6, 6, 1])[0]
                if ci == 11 and d > today - timedelta(days=300):
                    continue                       # Canyon Vista went quiet (shows on the win-back list)
                inv += 1
                name = PROPS[ci][0]
                po = f"PO-{r.randint(1000, 9999)}"
                for li in r.sample(MIX[ci], k=r.randint(1, 3)):
                    cat, item, desc, lo, hi, qlo, qhi = ITEMS[li]
                    qty = r.randint(qlo, qhi)
                    price = round(r.uniform(lo, hi), 2)
                    rows.append({"Invoice #": f"INV-{inv}", "Date": d.isoformat(), "Customer": name,
                                 "Acct #": str(90001 + ci), "PO #": po, "Sales Rep": "DR", "Item #": item,
                                 "Description": desc, "Category": cat, "Qty": str(qty), "Unit Price": f"{price:.2f}",
                                 "Amount": f"{qty * price:.2f}", "Cost": f"{qty * price * r.uniform(0.62, 0.74):.2f}"})
        d += timedelta(days=1)
    # an account that's in the company system but not in the app yet ("not linked")
    for m in range(1, 7):
        dd = date(today.year - 1, m * 2 - 1, 12)
        rows.append({"Invoice #": f"INV-6{m:04d}", "Date": dd.isoformat(), "Customer": "Rio Salado Townhomes (demo)",
                     "Acct #": "", "PO #": "", "Sales Rep": "DR", "Item #": "WS-BRZ",
                     "Description": "Window screen bronze frame charcoal mesh", "Category": "Screens", "Qty": "12",
                     "Unit Price": "21.00", "Amount": "252.00", "Cost": "160.00"})
    from .sales import guess_mapping
    import_rows(rows, guess_mapping(list(rows[0].keys())), batch="demo")
    # a custom goal month so the goals page shows both kinds
    x("INSERT OR REPLACE INTO sales_goals(period, goal, rate) VALUES (?,?,?)",
      (f"{today.year}-06", 55000, 3.5))


def _seed_quotes_prices_deliveries(cids, today):
    from . import deliveries as DL
    from . import pricing as P
    from . import quotes as QT
    P.set_agreed(cids[1], "VB-35-ALB", 'Vertical blind 3-1/2" smooth alabaster', "Blinds & Shades", 99.00,
                 "2026 portfolio pricing (demo)")
    P.set_agreed(cids[1], "D-3080-HC6", "30x80 hollow core 6 panel primecoat", "Doors", 58.50, "2026 portfolio pricing (demo)")
    fid = q("SELECT id FROM floorplans WHERE customer_id=? AND name='B2'", (cids[0],), one=True)["id"]
    q1 = QT.create(cids[0], "Turn kit - floorplan B2 x 4 units", "turn kit", fid, 4)
    QT.add_floorplan(q1, cids[0], fid, 4)
    q2 = QT.create(cids[1], "Spring blind refresh, building 2", "manual")
    for item, desc, cat, qty in (("VB-35-ALB", 'Vertical blind 3-1/2" smooth alabaster', "Blinds & Shades", 12),
                                 ("FW-2-WHT", '2" faux wood blind white', "Blinds & Shades", 24),
                                 ("D-3080-HC6", "30x80 hollow core 6 panel primecoat", "Doors", 6)):
        price, src, it = QT.priced(cids[1], desc, cat, item)
        QT.add_line(q2, desc, qty, "ea", it, cat, price, src)
    x("UPDATE quotes SET status='Sent', sent_at=? WHERE id=?", (now(), q2))
    s = q("SELECT id FROM sales WHERE customer_id=? ORDER BY invoice_date DESC LIMIT 1", (cids[3],), one=True)
    q3 = QT.create(cids[3], "Re-quote of last invoice", "invoice")
    if s:
        QT.add_invoice(q3, cids[3], s["id"])
    x("UPDATE quotes SET status='Approved', decided_at=? WHERE id=?", (now(), q3))
    # this morning's delivery list (made up): two by PO / SO number, one by name only
    sh = q("SELECT o.*, c.name cname FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.status IN "
           "('Shipped','Confirmed','Backordered','Sent') ORDER BY o.id", ())
    lines = ["DELIVERIES - TRUCK 2", "Stop  Customer                     PO / Order       Items"]
    if len(sh) >= 1:
        lines.append(f"1     {sh[0]['cname']}       {sh[0]['po']}         3 pcs")
    if len(sh) >= 2:
        lines.append(f"2     {sh[1]['cname']}       SO {sh[1]['vendor_ref'].replace('SO-', '')}       1 pc")
    lines.append("3     Rio Salado Townhomes (demo)   PO-9999          12 screens")
    if len(sh) >= 4:
        lines.append(f"4     {sh[3]['cname']}       (no PO)          blinds")
    DL.record("\n".join(lines), "email", "Deliveries for today - Truck 2 (demo)", now()[:16], "demo-delivery-1",
              auto_apply=True)
    _seed_items(today)
    x("""UPDATE account_holds SET days_late=?, aging=?, last_payment=? WHERE name='Canyon Vista Apartments'""",
      (75, dumps({"31-60": 1630.10, "61-90": 2688.45}), (today - timedelta(days=80)).isoformat()))


def _seed_items(today):
    """Items & Stock page (made up): list price, stock, margin for every item the demo sales history sold."""
    r = random.Random(7)
    vendors = ["Desert Supply Co (demo)", "Valley Door Works (demo)", "Sun Blind Mfg (demo)"]
    rows = q("""SELECT l.item_no, MAX(l.description) d, MAX(l.category) c, AVG(l.unit_price) p, MAX(s.invoice_date) last,
                SUM(l.qty) n FROM sales_lines l JOIN sales s ON s.id=l.sale_id
                WHERE l.item_no<>'' AND l.unit_price IS NOT NULL GROUP BY l.item_no""")
    for it in rows:
        on_hand = r.choice([0, 2, 5, 12, 24, 40, -3])
        on_so = r.choice([0, 0, 2, 6])
        x("""INSERT OR REPLACE INTO items(item_no, description, vendor, list_price, category, on_hand, on_so, available,
             on_po, reorder_pt, next_deliv, sales_week, qty_sold, last_sold, avg_price, avg_cost, margin_pct,
             price_updated, stock_updated, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (it["item_no"], it["d"], r.choice(vendors), round(it["p"] * 1.05, 2), it["c"], on_hand, on_so,
           on_hand - on_so, r.choice([0, 0, 12, 24]), 4, (today + timedelta(days=r.randint(2, 20))).isoformat(),
           round(r.uniform(0.2, 6), 1), it["n"], it["last"], round(it["p"], 2), round(it["p"] * 0.6, 2), 40.0,
           now(), now(), now()))


def _seed_review(cids):
    """One pending item on the Review screen."""
    from .changes import add_change, new_batch
    c = q("SELECT * FROM customers WHERE id=?", (cids[2],), one=True)
    b = new_batch("import", "Demo: new phone number from an order email")
    add_change(b, "set_field", customer_id=c["id"], field="phone", old=c["phone"], new="480-555-0199")


def reset(db_path):
    """Empty every table (Windows won't delete an open database file) and fill it again. Demo copy only."""
    from .db import get_db
    con = get_db()
    con.execute("PRAGMA foreign_keys=OFF")
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        if t not in ("users",):
            con.execute(f'DELETE FROM "{t}"')
    con.commit()
    con.execute("PRAGMA foreign_keys=ON")
    seed(db_path)
