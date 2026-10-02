"""Builds the demo kit (all customers, people and numbers are made up).
Run:  python demo/make_demo.py
"""
import csv
import os
import sys
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

COLS = ["Property", "Acct #", "Address", "City", "State", "Zip", "Mgmt Co", "Phone", "Email",
        "Contact", "Title", "Contact Phone", "Contact Email",
        "Floorplan", "Unit", "Building",
        "Product", "Room", "Qty", "Item #", "Width", "Height", "Color", "Style", "Finish", "Core", "Swing",
        "Door Location", "Thickness", "Hinge Dimension", "Lock Basket", "Mount", "Slat Width", "Headrail Width",
        "Valance Type", "Frame Style", "Frame Finish", "Screen Color", "Screen Type", "Frame Size", "Length",
        "Verified", "PO", "Date", "Status", "Notes"]

SAG = dict(Property="Saguaro Ridge Apartments (DEMO)", **{"Acct #": "90001"}, Address="4410 N Demo Canyon Rd",
           City="Phoenix", State="AZ", Zip="85018", **{"Mgmt Co": "Desert Sky Living (demo)"},
           Phone="602-555-0101", Email="office@saguaroridge.example")
PALO = dict(Property="Palo Verde Commons (DEMO)", **{"Acct #": "90002"}, Address="1200 E Sample Pkwy",
            City="Tempe", State="AZ", Zip="85281", **{"Mgmt Co": "Copper State Residential (demo)"},
            Phone="480-555-0102", Email="leasing@paloverde.example")
LOFT = dict(Property="Camelback Lofts (DEMO)", **{"Acct #": "90003"}, Address="7000 E Example Ave",
            City="Scottsdale", State="AZ", Zip="85251", **{"Mgmt Co": "Desert Sky Living (demo)"},
            Phone="480-555-0103")
MESA = dict(Property="Mesa Verde Villas (DEMO)")          # intentionally missing info

rows = []


def r(base, **kw):
    d = dict(base)
    d.update(kw)
    rows.append(d)


# ---------------- Saguaro Ridge: two floorplans, full measurements -------------
mgr = {"Contact": "Dana Whitfield", "Title": "Property Manager", "Contact Phone": "602-555-0111",
       "Contact Email": "dana@saguaroridge.example"}
sup = {"Contact": "Luis Ortega", "Title": "Maintenance Supervisor", "Contact Phone": "602-555-0112",
       "Contact Email": "luis@saguaroridge.example"}
for fp, units, beds in (("A1 - 1BR", ["101", "102", "103", "104", "105", "106", "107", "108"], 1),
                        ("B2 - 2BR", ["201", "202", "203", "204", "205", "206", "207", "208"], 2)):
    # unit list (one row per unit, links unit -> floorplan)
    for u in units:
        r(SAG, Floorplan=fp, Unit=u, Building="1" if u.startswith("1") else "2")
    big = fp.startswith("B2")
    r(SAG, **mgr, Floorplan=fp, Product="Interior Door", Room="Bedroom", Qty=str(beds), **{"Item #": "6P-3080-HC"},
      Width="30", Height="80", Style="Colonist", Finish="Primecoat", Core="Hollow Core", Swing="Left Hand",
      **{"Door Location": "Interior", "Thickness": '1-3/8"', "Hinge Dimension": '3-1/2"', "Lock Basket": '2-3/8"'},
      Verified="Yes", Notes="Measured on walk 2026-03")
    r(SAG, **sup, Floorplan=fp, Product="Bi-Pass Door", Room="Bedroom Closet", Qty="2", **{"Item #": "BP-2480"},
      Width="24", Height="80", Style="Colonist", Finish="Primecoat", Core="Hollow Core",
      Thickness='1-3/8"', Verified="Yes")
    r(SAG, Floorplan=fp, Product="Vertical Blind", Room="Living Room", Qty="1", **{"Item #": "VB-ALB-35"},
      Width="96" if big else "72", Height="84", Color="Alabaster", Mount="Inside Mount",
      **{"Slat Width": '3-1/2"', "Headrail Width": '1-3/8"', "Valance Type": "Standard Valance"}, Verified="Yes")
    r(SAG, Floorplan=fp, Product="Horizontal Blind", Room="Bedroom", Qty=str(beds), **{"Item #": "HB-FW-2"},
      Width="35", Height="48", Color="White", Style="Faux Wood", Verified="Yes")
    r(SAG, Floorplan=fp, Product="Window Screen", Room="Bedroom", Qty=str(beds), Width="34 1/4", Height="46 3/4",
      **{"Frame Finish": "Bronze", "Screen Color": "Charcoal", "Screen Type": "Bug Screen", "Frame Size": '5/16"'},
      Verified="Yes")
    r(SAG, Floorplan=fp, Product="Screen Door", Room="Patio", Qty="1", Width="36", Height="80",
      **{"Frame Style": "600 Series (Standard)", "Frame Finish": "Bronze", "Screen Color": "Charcoal",
         "Screen Type": "Sun Screen"}, Verified="Yes")
    r(SAG, Floorplan=fp, Product="Pre-Hung Door", Room="Entry", Qty="1", **{"Item #": "PH-3680-SC"},
      Width="36", Height="80", Style="Six Panel Steel", Core="Solid Core", Swing="Right Hand",
      **{"Door Location": "Exterior", "Thickness": '1-3/4"', "Hinge Dimension": '4"', "Lock Basket": '2-3/4"'},
      Verified="Yes")
    r(SAG, Floorplan=fp, Product="Baseboard", Room="Whole unit", Qty="1", Length="168 LF" if big else "112 LF",
      Style='3-1/4" colonial MDF', Color="White")

# a unit that differs from its floorplan (override)
r(SAG, Notes="Delivery: Mon-Fri 9am-4pm only. Prefers Alabaster verticals.")
r(SAG, Unit="108", Product="Bi-Pass Door", Room="Bedroom Closet", Qty="2", Width="30", Height="80",
  Style="Colonist", Finish="Primecoat", Core="Hollow Core", Verified="Yes",
  Notes="Unit 108 was renovated - wider closet than the rest of A1")

# order history (feeds the smart dropdowns + order tracking)
for po, date, unit, prod, extra, st in [
    ("SR-24811", "01/14/2026", "104", "Interior Door", dict(Width="30", Height="80", Swing="Left Hand", Qty="1"), "Completed"),
    ("SR-24907", "02/03/2026", "203", "Vertical Blind", dict(Width="96", Height="84", Color="Alabaster", Qty="1"), "Completed"),
    ("SR-25120", "03/22/2026", "106", "Horizontal Blind", dict(Width="35", Height="48", Color="White", Qty="2"), "Completed"),
    ("SR-25388", "05/09/2026", "207", "Screen Door", dict(Width="36", Height="80", **{"Frame Finish": "Bronze"}, Qty="1"), "Installed"),
    ("SR-25702", "07/18/2026", "102", "Interior Door", dict(Width="30", Height="80", Swing="Right Hand", Qty="1"), "Completed"),
    ("SR-26015", "09/02/2026", "205", "Window Screen", dict(Width="34 1/4", Height="46 3/4", Qty="2"), "Shipped"),
]:
    r(SAG, PO=po, Date=date, Status=st, Unit=unit, Product=prod, Room="", **extra)

# ---------------- Palo Verde: different products, delivery notes -------------
pv_mgr = {"Contact": "Renee Castillo", "Title": "Property Manager", "Contact Phone": "480-555-0121",
          "Contact Email": "renee@paloverde.example"}
for fp, units in (("Ocotillo", ["A101", "A102", "A103", "A104"]), ("Mesquite", ["B201", "B202", "B203"])):
    for u in units:
        r(PALO, Floorplan=fp, Unit=u, Building=u[0])
    r(PALO, **pv_mgr, Floorplan=fp, Product="Cabinet Door", Room="Kitchen", Qty="14", Width="15", Height="30",
      Style="Shaker", Color="Espresso", Finish="Thermofoil", Verified="Yes")
    r(PALO, Floorplan=fp, Product="Storage Door", Room="Patio Storage", Qty="1", Width="28", Height="80",
      Style="Six Panel Steel", Core="Solid Core", Swing="Left Hand", **{"Door Location": "Exterior"}, Verified="Yes")
    r(PALO, Floorplan=fp, Product="Vertical Blind", Room="Living Room", Qty="1", Width="70", Height="84",
      Color="White", **{"Slat Width": '3-1/2"', "Valance Type": "Upgrade Valance"}, Mount="Outside Mount",
      Verified="Yes")
r(PALO, Notes="Delivery: CALL RENEE 30 MIN BEFORE DELIVERY; deliver to leasing office")
for po, date, unit, prod, extra, st in [
    ("PV-1101", "04/11/2026", "A103", "Cabinet Door", dict(Width="15", Height="30", Qty="6"), "Completed"),
    ("PV-1188", "08/27/2026", "B202", "Vertical Blind", dict(Width="70", Height="84", Color="White", Qty="1"), "Backordered"),
]:
    r(PALO, PO=po, Date=date, Status=st, Unit=unit, Product=prod, **extra)

# ---------------- Camelback Lofts: contractor-style notes, garage doors -------------
r(LOFT, Contact="Marcus Lee", Title="Maintenance Supervisor", **{"Contact Phone": "480-555-0131"},
  Floorplan="Loft 1", Unit="L1", Product="Garage Door", Room="Garage", Qty="1", Width="96", Height="84",
  Style="Raised panel steel", Color="Sandstone", Verified="Yes")
r(LOFT, Notes="Delivery: MUST HAVE PO# & UNIT#; gate code 4321#")
for u in ("L1", "L2", "L3", "L4"):
    r(LOFT, Floorplan="Loft 1", Unit=u)
r(LOFT, Floorplan="Loft 1", Product="Entry Door", Room="Front", Qty="1", Width="36", Height="80",
  Style="Six Panel Steel", Core="Solid Core", Swing="Left Hand", **{"Door Location": "Exterior"},
  **{"Lock Basket": '2-3/8"'}, Verified="Yes")
r(LOFT, PO="CL-77", Date="06/30/2026", Status="Confirmed", Unit="L3", Product="Garage Door", Qty="1",
  Width="96", Height="84", Color="Sandstone")

# ---------------- Mesa Verde: barely any info (shows "Missing" + review fill-ins later) ---------
r(MESA, Contact="Pat Nguyen", Title="Leasing Agent")

with open(os.path.join(HERE, "1 - Demo customers and order history.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    for row in rows:
        w.writerow({c: row.get(c, "") for c in COLS})

# ---------------- second file: updates (shows the review screen) ----------------
upd = [
    dict(Property="Mesa Verde Villas (DEMO)", **{"Acct #": "90004"}, Address="88 W Placeholder St", City="Mesa",
         State="AZ", Zip="85201", **{"Mgmt Co": "Sunbelt Demo Management"}, Phone="480-555-0104",
         Contact="Pat Nguyen", **{"Contact Phone": "480-555-0141", "Contact Email": "pat@mesaverde.example"}),
    dict(Property="Saguaro Ridge Apartments (DEMO)", Phone="602-555-0199",          # conflict: new office number
         Contact="Kim Barrett", Title="Assistant Manager", **{"Contact Email": "kim@saguaroridge.example"}),
    dict(Property="Ironwood Flats (DEMO)", **{"Acct #": "90005"}, Address="300 S Mockup Blvd", City="Phoenix",
         State="AZ", Zip="85004", **{"Mgmt Co": "Copper State Residential (demo)"}, Phone="602-555-0105",
         Contact="Jordan Ellis", Title="Property Manager"),
]
with open(os.path.join(HERE, "2 - Demo updates (review screen).csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    for row in upd:
        w.writerow({c: row.get(c, "") for c in COLS})

# ---------------- sample customer email with a filled-out form attached ----------------
from orderapp.pdf_fill import fill_form  # noqa: E402

pdf, _ = fill_form("window_screen", {
    "header": {"name": "Saguaro Ridge Apartments (DEMO)", "acct": "90001", "po": "SR-26140", "date": "10/01/2026"},
    "comments": "Filled out by Luis - please rush",
    "blocks": [{"qty": "3", "width": "34 1/4", "height": "46 3/4", "frame_finish": "Bronze",
                "screen_color": "Charcoal", "screen_type": "Bug Screen", "frame_size": '5/16"'}]})
m = EmailMessage()
m["From"] = "Luis Ortega <luis@saguaroridge.example>"
m["To"] = "taylor@example.com"
m["Subject"] = "Order request - PO SR-26140 (demo)"
m.set_content("""Hi Taylor,

PO SR-26140

Unit 104 needs 1 new bedroom door, left hand.
Unit 206 - vertical blind living room 96 x 84 alabaster

Also attached the window screen form for 205.

Thanks,
Luis Ortega
Maintenance Supervisor, Saguaro Ridge (demo)
602-555-0112
""")
m.add_attachment(pdf, maintype="application", subtype="pdf", filename="Window screens SR-26140.pdf")
with open(os.path.join(HERE, "3 - Demo customer email.eml"), "wb") as f:
    f.write(bytes(m))

# ---------------- sample "field photo" of a handwritten order ----------------
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

img = Image.new("RGB", (1500, 1100), (250, 248, 238))
d = ImageDraw.Draw(img)
for y in range(140, 1100, 60):
    d.line([(60, y), (1440, y)], fill=(190, 205, 230), width=2)
d.line([(140, 0), (140, 1100)], fill=(230, 160, 160), width=3)
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", 46)
    big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf", 56)
except OSError:
    font = big = ImageFont.load_default()
lines = [("FIELD ORDER - Palo Verde Commons (DEMO)", big), ("PO PV-1210", font), ("", font),
         ("Unit A102 - kitchen cabinet doors x4", font), ("   15 x 30  Shaker  Espresso", font),
         ("Unit B203 - patio storage door 28 x 80 LH", font), ("   solid core, steel", font), ("", font),
         ("Call Renee 30 min before delivery", font)]
y = 70
for text, fnt in lines:
    d.text((170, y), text, fill=(30, 40, 110), font=fnt)
    y += 85
img.save(os.path.join(HERE, "4 - Demo field photo.jpg"), quality=85)
print("Demo kit written to", HERE)
