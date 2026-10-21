"""Order form catalog.

Maps each supplied PDF order form to *logical* field keys so one set of
customer / measurement data can fill any form.

Field kinds
-----------
text    -> a PDF text widget (or an overlay position when the PDF has no widget)
choice  -> a group of PDF checkboxes; one option is selected. An option may tick
           several boxes (list of names). Options may also be overlay marks.
bool    -> a single checkbox
lines   -> a long text spread over several PDF text widgets (comments)

Overlay entries are used where the PDF has a printed label but no fillable
widget (the Pre-Hung form reuses the Door form's widgets, so most of its
measurement rows have no fields). Coordinates are PDF points, origin top-left,
(x, baseline_y, max_width).
"""

from copy import deepcopy

# --------------------------------------------------------------------------
# Shared option lists
# --------------------------------------------------------------------------
DOOR_FINISHES = ["Primecoat", "Embossed Primecoat", "Oak Legacy", "Walnut Legacy"]
YES_NO = ["Y", "N"]
YN = [("Yes", None), ("No", None)]
SUNSCREEN_COLORS = ["Black", "Brown", "Beige", "Stucco", "Gray", "Dark Bronze"]
HARDWARE_COLLECTIONS = ["Marina", "Soma", "Lombard", "Sea Cliff", "Tiburon"]
BATH_COLLECTIONS = [("Waterfront", "Satin Nickel"), ("Soma", "Matte Black"), ("Palo Alto", "Satin Nickel"),
                    ("Tiburon", "Matte Black"), ("Santa Cruz", "Satin Nickel")]
TRIM_PROFILES = [
    ("A", '#103 Casing MDF Ultralight 103MUL 9/16" x 2-1/4"'),
    ("B", '#356 Casing MDF Ultralight 152MUL 5/8" x 2-1/4"'),
    ("C", '#366 Casing MDF Ultralight 162MUL-2 5/8" x 2-1/4"'),
    ("D", '#366 Casing MDF Ultralight 162MUL-3 5/8" x 3-1/4"'),
    ("E", 'Cambridge Casing MDF Ultralight 161MUL 11/16" x 3-1/4"'),
    ("F", '1" Eased Edge Craftsman Base 387MUL-3 1/2" x 3-1/2"'),
    ("G", '1" Eased Edge Craftsman Base 387MUL-4 1/2" x 4-1/2"'),
    ("H", '1" Eased Edge Craftsman Base 387MUL-5 1/2" x 5-1/2"'),
    ("I", '#711 Base MDF Ultralight 311MUL 3/8" x 3-1/2"'),
    ("J", 'Victorian/Newport Base 329MUL 9/16" x 3-7/8"'),
    ("K", 'Flat / Square Edge (modern) - pick thickness & height'),
]
# flat / square-edge modern trim (newer apartments and houses) - common sizes; type the exact size if different
FLAT_TRIM_PROFILE = "K: " + TRIM_PROFILES[-1][1]
FLAT_TRIM_THICK = ['1/2"', '9/16"', '5/8"', '3/4"']
FLAT_TRIM_HEIGHT = ['2-1/4"', '3-1/4"', '3-1/2"', '4"', '4-1/4"', '5-1/4"', '5-1/2"']
DOOR_MATERIALS = ["Wood", "Metal", "Fiberglass"]
COUNTERTOP_MATERIALS = ["Laminate", "Synthetic", "Thinscape", "Custom"]
PREFAB_KITCHEN_ITEMS = ["Base Cabinet", "Sink Base", "Drawer Base", "Wall Cabinet"]
PREFAB_VANITY_ITEMS = ["Single Box", "Double Box", "Double Box with Sink", "Extended Double Box with Sink",
                       "Doors Only"]
CLOSET_TYPES = ["Bypass (standard)", "Bypass (custom)", "Bi-Fold (standard)", "Bi-Fold (custom)"]
WARDROBE_TYPES = ["Mirrored Bypass Door", "Mirrored Swing Door", "White Vinyl Board Door"]   # brochure: Wardrobe & Closet
WARDROBE_FINISHES = ["White", "Satin Nickel", "Aztec Gold", "Other"]
SHOWER_TYPES = ["Sliding (bypass) door", "Swing / pivot door", "Enclosure (door + panels)", "Fixed panel"]
SHOWER_FRAMES = ["Framed", "Semi-Frameless", "Frameless"]
SHOWER_FINISHES = ["Chrome", "Satin Nickel", "Matte Black", "Other"]
SHOWER_GLASS = ["Clear", "Obscure / Rain", "Other"]
NEW_DOOR_TYPES = ["5 Panel", "2 Panel", "2 Panel Arch", "2 Panel Arch Plank", "5 Panel Shaker",
                  "3 Panel Shaker Equal", "3 Panel Shaker Craftsman", "2 Panel Shaker", "1 Panel Shaker",
                  "HC Primecoat"]
# v2.12: door style (the design) and panel count are separate, so any combination can be ordered
DOOR_DESIGNS = ["Shaker", "Shaker Craftsman", "Shaker Equal", "Arch", "Arch Plank", "Plank", "Smooth", "Camden",
                "Carrara", "Cheyenne", "Colonial", "Coventry", "Cremona", "Lincoln Park", "Riverside", "Rockport",
                "Roman"]
DOOR_PANELS = ["1 Panel", "2 Panel", "3 Panel", "4 Panel", "5 Panel", "6 Panel", "Flush"]
DOOR_TOPS = ["Square Top", "Arched Top"]
# door styles saved before the split -> (style, panels, top)
OLD_DOOR_STYLES = {"5 Panel": ("", "5 Panel", ""), "2 Panel": ("", "2 Panel", ""),
                   "2 Panel Arch": ("Arch", "2 Panel", "Arched Top"),
                   "2 Panel Arch Plank": ("Arch Plank", "2 Panel", "Arched Top"),
                   "5 Panel Shaker": ("Shaker", "5 Panel", ""), "3 Panel Shaker Equal": ("Shaker Equal", "3 Panel", ""),
                   "3 Panel Shaker Craftsman": ("Shaker Craftsman", "3 Panel", ""),
                   "2 Panel Shaker": ("Shaker", "2 Panel", ""), "1 Panel Shaker": ("Shaker", "1 Panel", ""),
                   "HC Primecoat": ("Smooth", "Flush", ""), "Flat (HC Primecoat)": ("Smooth", "Flush", ""),
                   "6 Panel": ("Colonist", "6 Panel", "")}
NEW_BLIND_STYLES = ["Basswood", "Metal Plus"]
BLIND_SLATS = ['1"', '2"', '2-1/2"']
# styles that used to include the slat size (before v2.12) -> (style, slat size)
OLD_BLIND_STYLES = {'Basswood 2"': ("Basswood", '2"'), 'Basswood 2-1/2"': ("Basswood", '2-1/2"'),
                    '1" Metal Plus Mini': ("Metal Plus", '1"'), '2" Metal Plus': ("Metal Plus", '2"')}
HORIZONTAL_STYLES = ['2" Faux Wood', 'Premium Basswood 2"', 'Premium Basswood 2-1/2"', '1" Aluminum Mini Blind',
                     '1" Vinyl Mini Blind', '1" Metal Plus Mini Blind', '2" Metal Plus Blind']
CABINET_STYLES = ["Shaker", "Shaker Slim", "Italia", "Bradford", "Elegante", "Elegante Gauntlet Supermatte"]

HEADER_STD = {
    "name": "Name", "acct": "Acct #", "address": "Addr", "city": "City",
    "state": "State", "zip": "Zip", "mgmt": "Mgmt", "phone": "Phone",
    "po": "PO", "date": "Date",
}

HEADER_FIELD_LABELS = {
    "name": "Customer / Property Name", "acct": "Acct #", "address": "Address",
    "city": "City", "state": "State", "zip": "Zip", "mgmt": "Mgmt Co.",
    "phone": "Phone", "po": "P.O. Number", "date": "Date",
    "sales_rep": "Sales Rep", "comments": "Comments",
}


def _slug(s):
    import re
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def _t(key, label, pdf, **kw):
    d = {"key": key, "label": label, "kind": "text", "pdf": pdf}
    d.update(kw)
    return d


def _x(key, label, kind="text", options=None, **kw):
    d = {"key": key, "label": label, "kind": kind, "pdf": None, "extra": True}
    if options is not None:
        d["options"] = options
    d.update(kw)
    return d


def _c(key, label, options, **kw):
    """options: list of (label, pdf_name | [pdf_names] | {"mark": (x, y)})"""
    d = {"key": key, "label": label, "kind": "choice", "options": options}
    d.update(kw)
    return d


def _b(key, label, pdf, **kw):
    d = {"key": key, "label": label, "kind": "bool", "pdf": pdf}
    d.update(kw)
    return d


def _ov(x, y, w, size=9):
    return {"overlay": (x, y, w), "size": size}


# --------------------------------------------------------------------------
# Block builders (one "block" = one line item on the form)
# --------------------------------------------------------------------------
def _bypass_block(i):
    s = f"1.{i}"
    return [
        _t("qty", "Quantity", f"Qty{s}", w=4),
        _t("item_no", "Item #", f"Item#.{i}"),
        _c("finish", "Finish", [("Primecoat", f"PrimeCoat{s}"), ("Embossed Primecoat", f"Embossed{s}"),
                                ("Oak Legacy", f"OakLeg{s}"), ("Walnut Legacy", f"WalLeg{s}")]),
        _c("style", "Door Style", [
            ("Six-Panel Embossed (Colonist)", f"SixPanEmb{s}"),
            ("Two-Panel Embossed (Classique)", f"TwoPanEmb{s}"),
            ("Four-Panel Embossed (Carmelle)", f"FourPanEmb{s}"),
            ("Three-Panel Embossed (Clermont)", f"ThreePanEmb{s}"),
            ("Other", f"Other{s}")], other_option="Other", other_field="style_other", photos="door_style"),
        _t("style_other", "Style - Other", f"OtherStyInfo{s}"),
        _c("core", "Core Type", [("Hollow Core", f"HC{s}"), ("Solid Core", f"SC{s}")]),
        _c("door_location", "Door Location", [("Interior", f"Interior{s}")]),
        _t("width", "Door Width (in)", f"DoorWidth{s}", measure=True),
        _t("height", "Door Height (in)", f"DoorHeight{s}", measure=True),
        _c("finger_pull", "Finger Pull", [('2-1/8"', f"FP2-1/8_{s}"), ('1/2"', f"FP1/2_{s}")]),
        _c("thickness", "Door Thickness", [('1-3/8"', f"DT1-3/8_{s}"), ('1-3/4"', f"DT1-3/4_{s}"),
                                           ("Other", f"DTOther{s}")],
           other_option="Other", other_field="thickness_other"),
        _t("thickness_other", "Thickness - Other", f"DTOtherTxt{s}"),
        _t("finger_pull_dist", "Top of door to center of finger pull (in)", f"FPTopDist{s}", measure=True),
    ]


def _door_common(prehung=False):
    f = [
        _t("qty", "Quantity", "Qty1.0", w=4),
        _t("item_no", "Item #", "Item#"),
        _c("finish", "Finish", [("Primecoat", "PrimeCoat1.0"), ("Embossed Primecoat", "Embossed1.0"),
                                ("Oak Legacy", "OakLeg1.0"), ("Walnut Legacy", "WalLeg1.0")]),
        _c("style", "Door Style", [
            ("Six-Panel Embossed (Colonist)", "SixPanEmb1.0"),
            ("Two-Panel Embossed (Classique)", "TwoPanEmb1.0"),
            ("Four-Panel Embossed (Carmelle)", "FourPanEmb1.0"),
            ("Three-Panel Embossed (Clermont)", "ThreePanEmb1.0"),
            ("Six Panel Steel", "SixPanSteel1.0")] +
           [(t, f"DoorStyle {_slug(t)}.0") for t in DOOR_DESIGNS] +
           [("Other", "OtherDoor1.0")], other_option="Other", other_field="style_other", photos="door_style"),
        _t("style_other", "Style - Other", "OtherDoorTXT.0"),
        _c("panels", "Panels", [(t, f"DoorPanels {_slug(t)}.0") for t in DOOR_PANELS]),
        _c("door_top", "Panel Top", [(t, f"DoorTop {_slug(t)}.0") for t in DOOR_TOPS]),
        _c("material", "Door Material", [(m, f"Material {_slug(m)}.0") for m in DOOR_MATERIALS]),
        _c("core", "Core Type", [("Hollow Core", "HC_Door.0"), ("Solid Core", "SC_Door.0")]),
        _c("door_location", "Door Location", [("Interior", "InteriorDoor.0"), ("Exterior", "Exterior_Door.0")]),
        _c("swing", "Door Swing", [("Left Hand", "LeftHand_Door.0"), ("Right Hand", "RightHand_Door.0")]),
    ]
    if prehung:
        f += [
            _c("swing_dir", "Swing In / Out", [("Swing In", {"mark": (106, 335)}),
                                               ("Swing Out", {"mark": (106, 346)})]),
            _c("jamb", "Jamb Type", [("Flat", {"mark": (450, 287)}), ("Kerfed", {"mark": (450, 298)})]),
            _t("rough_width", "Rough Opening Width (in)", "RoughWidth.0", measure=True),
            _t("rough_height", "Rough Opening Height (in)", "RoughHeight.0", measure=True),
        ]
    f += [
        _t("hinge1", "Top of door to top of 1st hinge (in)", "Hinge1.0", measure=True),
        _t("hinge2", "Top of door to top of 2nd hinge (in)", "Hinge2.0", measure=True),
        _t("hinge3", "Top of door to top of 3rd hinge (in)", "Hinge3.0", measure=True),
        _t("hinge4", "Top of door to top of 4th hinge (in) - if it has one", "Hinge4.0", measure=True),
    ]
    f += [
        _t("width", "Door Width (in)", "DoorWidth.0", measure=True),
        _t("height", "Door Height (in)", "DoorHeight.0", measure=True),
    ]
    f += [   # bores: each one measured from the top of the door to its center (same on both forms)
        _t("one_sided_deadbolt", "Top of door to center of one-sided deadbolt (in)",
           "OneSided_Deadbolt.0", measure=True),
        _t("deadbolt", "Top of door to center of deadbolt (in)", "Deadbolt.0", measure=True),
        _t("entry_lock", "Top of door to center of entry lock (in)", "EntryLock.0", measure=True),
    ]
    if prehung:
        peep = ("peephole_yn", ["Y", "Yes", "y", "yes", "YES"])
        f += [
            _t("threshold_type", "Threshold Type (Adjustable, ADA)", "ThresholdType.0", suggest=["Adjustable", "ADA"]),
            _t("threshold_color", "Threshold Color (Mill, Bronze)", "ThresholdColor.0", suggest=["Mill", "Bronze"]),
            _t("threshold_size", "Threshold Size (width x length, in)", "ThresholdSize.0",
               placeholder='e.g. 5-5/8" x 36"'),
            _t("threshold_thick", "Threshold Thickness / Height (in)", "ThresholdThick.0", placeholder='e.g. 1/2"'),
            _t("hinge_color", "Hinge Color (Satin Nickel, Brass, Chrome)", "HingeColor.0",
               suggest=["Satin Nickel", "Brass", "Chrome"]),
            _t("self_closing", "Self Closing Hinges (Y/N)", "SelfClosingYN.0", suggest=YES_NO),
            _t("nrp_hinges", "NRP Hinges (Y/N)", "NRPHinges.0", suggest=YES_NO),
            _t("glass_insert", "Glass Insert (Full Ten Lite, Full One Lite, Half Lite)", "GlassInsert.0",
               suggest=["Full Ten Lite", "Full One Lite", "Half Lite", "None"]),
            _t("peephole_yn", "Peep Hole (Y/N)", "PeepholeYN.0", suggest=YES_NO),
            _t("peephole", "Top of door to center of peephole (in)", "Peephole.0", measure=True, show_if=peep),
        ]
    else:
        f += [
            _t("peephole", "Top of door to center of peephole (in)", "Peephole.0", measure=True),
        ]
    f += [
        _c("lock_basket", "Lock Basket / Backset", [('2-3/8"', "LockBasket_2-3/8.0"),
                                                    ('2-3/4"', "LockBasket 2-3/4.0"),
                                                    ('5"', "LockBasket 5.0")]),
        _c("thickness", "Door Thickness", [('1-3/8"', "DoorThickness1-3/8.0"),
                                           ('1-3/4"', "DoorThickness1-3/4.0"),
                                           ("Other", "DoorThicknessOther.0")],
           other_option="Other", other_field="thickness_other"),
        _t("thickness_other", "Thickness - Other", None, **_ov(463, 563, 98)),
        _c("hinge_dim", "Hinge Dimension", [('3-1/2"', "HingeDim3-1/2.0"), ('4"', "HingeDim4.0"),
                                             ("Other", "HingeDimOther.0")],
           other_option="Other", other_field="hinge_dim_other"),
        _t("hinge_dim_other", "Hinge Dimension - Other (in)", "HingeDimOther (describe).0"),
        _c("hinge_radius", "Hinge Radius / Corners", [('1/4"', "HingeRad1-4.0"), ('5/8"', "HingeRad5/8.0"),
                                                      ("Square", "HingeRadSquare.0")]),
    ]
    return f


def _new_door_block():
    return [
        _t("qty", "Quantity", "Quantity", w=4),
        _t("item_no", "Item #", "Item #"),
        _c("style", "Door Style", [
            ("Carrera (2-Panel)", "Style Carrera (2-Panel)"),
            ("Camden", "Style Camden"),
            ("Riverside (5-Panel)", "Style Riverside (5-Panel)"),
            ("Colonist (6-Panel)", "Style Colonist (6-Panel)"),
            ("Steel Door", "Style Steel Door")], photos="door_style"),
        _c("finish", "Finish", [("Primecoat", "Finish Primecoat"),
                                ("Embossed Primecoat", "Finish Embossed Primecoat"),
                                ("Oak Legacy", "Finish Oak Legacy"), ("Other", "Finish Other")],
           other_option="Other", other_field="finish_other"),
        _t("finish_other", "Finish - Other", "Finish Other (describe)"),
        _x("material", "Door Material", "choice", [(m, None) for m in DOOR_MATERIALS]),
        _c("core", "Core Type", [("Solid Core", "Core Solid"), ("Hollow Core", "Core Hollow")]),
        _c("door_location", "Door Location", [("Interior", "Location Interior"), ("Exterior", "Location Exterior")]),
        _c("swing", "Door Swing", [("Left Hand", "Swing Left Hand"), ("Right Hand", "Swing Right Hand")]),
        _t("width", "Door Width (in)", "Door Width", measure=True),
        _t("height", "Door Height (in)", "Door Height", measure=True),
        _t("hinge1", "Top of door to top of 1st hinge (in)", "1st Hinge (in from top of door)", measure=True),
        _t("hinge2", "Top of door to top of 2nd hinge (in)", "2nd Hinge (in from top of door)", measure=True),
        _t("hinge3", "Top of door to top of 3rd hinge (in)", "3rd Hinge (in from top of door)", measure=True),
        _t("hinge4", "Top of door to top of 4th hinge (in) - if it has one", None, measure=True, extra=True),
        _t("peephole", "Top of door to center of peephole (in)", "Top of Door to Center of Peephole", measure=True),
        _t("one_sided_deadbolt", "Top of door to center of one-sided deadbolt (in)",
           "One-Sided Deadbolt Measurement", measure=True, auto_check="Bore One-Sided Deadbolt"),
        _t("deadbolt", "Top of door to center of deadbolt (in)", "Deadbolt Measurement",
           measure=True, auto_check="Bore Deadbolt"),
        _t("entry_lock", "Top of door to center of entry lock (in)", "Entry Lock Measurement",
           measure=True, auto_check="Bore Entry Lock"),
        _c("lock_basket", "Lock Basket / Backset", [('2-3/8"', 'Backset 2-3/8"'), ('2-3/4"', 'Backset 2-3/4"'),
                                                    ('5"', 'Backset 5"')]),
        _c("thickness", "Door Thickness", [('1-3/8"', 'Door Thickness 1-3/8"'), ('1-3/4"', 'Door Thickness 1-3/4"'),
                                           ("Other", ("Door Thickness Other", "CheckBox"))],
           other_option="Other", other_field="thickness_other"),
        _t("thickness_other", "Thickness - Other", ("Door Thickness Other", "Text")),
        _c("hinge_dim", "Hinge Dimension", [('3-1/2"', 'Hinge Dimension 3-1/2"'), ('4"', 'Hinge Dimension 4"'),
                                             ("Other", None)], other_option="Other", other_field="hinge_dim_other"),
        _x("hinge_dim_other", "Hinge Dimension - Other (in)"),
        _c("hinge_radius", "Hinge Radius / Corners", [('1/4"', 'Hinge Radius 1/4"'), ('5/8"', 'Hinge Radius 5/8"'),
                                                      ("Square", None)]),
    ]


SCREEN_DOOR_SUFFIX = [".0", ".1.0", ".1.1.0", ".1.1.1"]


def _screen_door_block(i):
    s = SCREEN_DOOR_SUFFIX[i]
    return [
        _t("qty", "Quantity", f"Quantity{s}", w=4),
        _t("item_no", "Item #", f"Item#.{i}"),
        _t("width", "Door Width (in)", f"DoorWidth{s}", measure=True),
        _t("height", "Door Height (in)", f"DoorHeight{s}", measure=True),
        _c("frame_style", "Frame Style", [("600 Series (Standard)", f"600Series{s}"),
                                          ("1250 Series (Extruded)", f"1250Series{s}")]),
        _c("frame_finish", "Trim / Frame Color", [("White", f"White{s}"), ("Tan", f"Tan{s}"), ("Bronze", f"Bronze{s}"),
                                                  ("Gray", f"Gray{s}"), ("Champagne", f"Champagne{s}"),
                                                  ("Almond", f"Almond{s}"), ("Mill", f"Mill{s}")],
           photos="patio_trim"),
        _c("screen_color", "Screen Color", [("Charcoal", f"Charcoal{s}"), ("Grey", f"Grey{s}")]),
        _c("screen_type", "Screen Type", [("Bug Screen", f"Bug Screen{s}"), ("Sun Screen", f"Sun{s}")]),
        {"key": "line_comments", "label": "Line Comments", "kind": "lines",
         "pdf": [f"ScrComments1{s}", f"ScrComments2{s}", f"ScrComments3{s}"], "chars": [55, 65, 65]},
    ]


VERT_SUFFIX = [".0", ".1.0", ".1.1.0", ".1.1.1.0", ".1.1.1.1"]


def _vert_block(i):
    s = VERT_SUFFIX[i]
    return [
        _t("qty", "Quantity", f"Qty{s}", w=4),
        _t("item_no", "Item #", f"Item#.{i}"),
        _t("room", "Location (room)", f"Location.{i}"),
        _t("width", "Actual Window Width (in) - inside mount", f"IMWidth{s}", measure=True),
        _t("height", "Actual Window Height (in) - inside mount", f"IMHeight{s}", measure=True),
        _t("om_headrail", "Headrail Length (in) - outside mount", f"OMHeadLength{s}", measure=True),
        _t("om_slat", "Slat Length (in) - outside mount", f"OMSlatLength{s}", measure=True),
        _c("valance", "Valance Type", [("Standard Valance", f"StandVal{s}"), ("Upgrade Valance", f"UpgrdVal{s}")]),
        _b("val_only", "Valance Only", f"Val Only.{i}"),
        _c("color", "Color", [("Alabaster", f"Alabaster{s}"), ("White", f"White{s}")]),
        _c("headrail_width", "Headrail Width", [('1-3/8"', f"Hed1-3/8{s}"), ('1-3/4"', f"Hed1-3/4{s}")]),
        _c("slat_width", "Slat Width", [('2"', f"Slat2{s}"), ('3-1/2"', f"Slat3-1/2{s}")]),
        _c("slat_style", "Slat Style", [("Ribbed", f"Ribbed{s}"), ("Smooth", f"Smooth{s}")]),
        _t("price", "Price / Each", f"Price{s}"),
    ]


VERT2_SUFFIX = ["1.0.0", "1.0.1.0", "1.0.1.1.0", "1.0.1.1.1.0", "1.0.1.1.1.1"]


def _vert2_block(i):
    s = VERT2_SUFFIX[i]
    return [
        _t("qty", "Quantity", f"QTY{s}", w=4),
        _t("room", "Location (room)", f"Location {s}"),
        _c("mount", "Installation", [("Inside Mount", f"Inside {s}"), ("Outside Mount", f"Outside {s}")]),
        _t("width", "Actual Window Width (in)", f"Act Width {s}", measure=True),
        _t("height", "Actual Window Height (in)", f"Act Height {s}", measure=True),
        _t("om_headrail", "Headrail Length (in) - outside mount", f"Headrail In. {s}", measure=True),
        _t("om_slat", "Slat Length (in) - outside mount", f"Slat Length OM {s}", measure=True),
        _c("color", "Color", [("Alabaster", f"Ala {s}"), ("White", f"White {s}")]),
        _c("headrail_width", "Headrail Width", [('1-3/8"', f"Headrail {s}"), ('1-3/4"', f"Width {s}")]),
        _c("slat_width", "Slat Width", [('2"', f'2" {s}'), ('3-1/2"', f'3.5" {s}')]),
        _c("slat_style", "Slat Style", [("Ribbed", f"Ribbed {s}"), ("Smooth", f"Smooth {s}")]),
        _t("price", "Price / Each", f"Price Ea. {s}"),
    ]


WS_SUFFIX = [".0", ".1.0", ".1.1"]


def _window_screen_block(i):
    s = WS_SUFFIX[i]
    loc = lambda kind, n, side: f"{kind}SpecLoc{side}{n}.0.{i}"  # noqa: E731
    return [
        _t("qty", "Quantity", f"Qty{s}", w=4),
        _t("item_no", "Item #", f"Item#.{i}"),
        _t("width", "Window Screen Width (in)", f"ScrWidth{s}", measure=True),
        _t("height", "Window Screen Height (in)", f"ScrHeight{s}", measure=True),
        _c("frame_size", "Frame Size", [('1/4"', f"FrmSze1/4{s}"), ('5/16"', f"FrmSze5/16{s}"),
                                        ('3/8"', f"FrmSze3/8{s}"), ('7/16"', f"FrmSze7/16{s}"),
                                        ('1"', f"FrmSze1{s}")]),
        _c("frame_finish", "Trim / Frame Color", [("White", f"FinishWhite{s}"), ("Tan", f"FinishTan{s}"),
                                                  ("Champagne", f"FinishChamp{s}"), ("Bronze", f"FinishBronze{s}"),
                                                  ("Mill", f"FinishMill{s}"), ("Almond", f"FinishAlmond{s}")],
           photos="sunscreen_trim"),
        _c("screen_color", "Screen Color", [("Charcoal", f"ColChar{s}"), ("Grey", f"ColGrey{s}"),
                                            ("Other", f"ColOther{s}")],
           other_option="Other", other_field="screen_color_other"),
        _t("screen_color_other", "Screen Color - Other", f"ColOtherTXT{s}"),
        _c("screen_type", "Screen Type", [("Bug Screen", f"TypBug{s}"), ("Sun Screen", f"TypSun{s}")]),
        _t("sun_percent", "Sun Screen %", f"TypSunPercent{s}"),
        _c("sun_fabric", "Sunscreen Fabric", [("Suntex 80", f"Suntex80{s}"), ("Suntex 90", f"Suntex90{s}")],
           show_if=("screen_type", ["Sun Screen"])),
        _c("sun_color", "Sunscreen Color", [(x, f"SunCol {_slug(x)}{s}") for x in SUNSCREEN_COLORS],
           photos="sunscreen_color", show_if=("screen_type", ["Sun Screen"])),
        _c("pull_tabs", "Pull Tabs", [
            ("1 Pull Tab - Height side", [f"Pull1Tab{s}", loc("PT", 1, "Height")]),
            ("1 Pull Tab - Width side", [f"Pull1Tab{s}", loc("PT", 1, "Width")]),
            ("2 Pull Tabs - Height side", [f"Pull2Tab{s}", loc("PT", 2, "Height")]),
            ("2 Pull Tabs - Width side", [f"Pull2Tab{s}", loc("PT", 2, "Width")])]),
        _c("half_moon", "Half Moon Clips", [
            ("1 Half Moon Clip - Height side", [f"HMClip1{s}", loc("HM", 1, "Height")]),
            ("1 Half Moon Clip - Width side", [f"HMClip1{s}", loc("HM", 1, "Width")]),
            ("2 Half Moon Clips - Height side", [f"HMClip2{s}", loc("HM", 2, "Height")]),
            ("2 Half Moon Clips - Width side", [f"HMClip2{s}", loc("HM", 2, "Width")])]),
        {"key": "other_mods", "label": "Other Modifications", "kind": "lines",
         "pdf": [f"OtherModTXT1{s}"], "chars": [80]},   # 2nd line is now the sunscreen line
    ]


def _horiz_block(i):
    n = i + 1
    p = f"Blind {n}"
    return [
        _t("qty", "Quantity", f"{p} Quantity", w=4),
        _t("item_no", "Item #", f"{p} Item #"),
        _t("unit", "Unit #", f"{p} Unit #"),
        _c("style", "Style", [("All Vinyl", f"{p} Style All Vinyl"), ("Vinyl Plus", f"{p} Style Vinyl Plus"),
                              ("All Metal", f"{p} Style All Metal"), ("Faux Wood", f"{p} Style Faux Wood")] +
           [(t, f"{p} Style {t}") for t in NEW_BLIND_STYLES]),
        _c("slat_size", "Slat Size", [(t, f"{p} Slat {t}") for t in BLIND_SLATS]),
        _c("room", "Location", [("Bedroom", f"{p} Location Bedroom"), ("Kitchen", f"{p} Location Kitchen"),
                                ("Living Room", f"{p} Location Living Room"), ("Other", f"{p} Location Other")],
           other_option="Other", other_field="room_other"),
        _t("bedroom_no", "Bedroom #", f"{p} Bedroom #"),
        _t("room_other", "Location - Other", f"{p} Location Other (describe)"),
        _c("color", "Color", [("Alabaster", f"{p} Color Alabaster"), ("White", f"{p} Color White")]),
        _t("height", "Actual Window Height (in)", f"{p} Window Height (in)", measure=True),
        _t("width", "Actual Window Width (in)", f"{p} Window Width (in)", measure=True),
        {"key": "line_comments", "label": "Comments", "kind": "lines", "pdf": [f"{p} Comments"], "chars": [95]},
    ]


# --------------------------------------------------------------------------
# Forms
# --------------------------------------------------------------------------
FORMS = {
    "bypass": {
        "title": "Bi-Pass Door Order Form", "file": "OF-Bypass Door.pdf",
        "header": dict(HEADER_STD), "sales_rep": "SR",
        "comments": {"pdf": ["Comment1", "Comments2", "Comments3"], "chars": [45, 55, 55]},
        "blocks": [_bypass_block(0), _bypass_block(1)],
        "products": ["bypass_door", "closet_door"],
    },
    "door": {
        "title": "Door Order Form", "file": "OF-Door.pdf",
        "header": dict(HEADER_STD), "sales_rep": "Sales Rep",
        "comments": {"pdf": ["Comments1", "Comments2", "Comments3"], "chars": [45, 55, 55]},
        "blocks": [_door_common(False)],
        "products": ["interior_door", "entry_door", "storage_door", "closet_door"],
    },
    "new_door": {
        "title": "Door Order Form (New Door)", "file": "OF-New Door Order Form.pdf",
        "header": {"name": "Name", "address": "Address", "po": "PO #", "date": "Date"},
        "address_combined": True, "sales_rep": "Sales Representative",
        "comments": None,
        "blocks": [_new_door_block()],
        "products": ["interior_door", "entry_door", "storage_door"],
    },
    "prehung": {
        "title": "Pre-Hung Door Form", "file": "OF-Pre-Hung.pdf",
        "header": dict(HEADER_STD), "sales_rep": "Sales Rep",
        "comments": {"pdf": ["Comments1", "Comments2", "Comments3"], "chars": [45, 55, 55]},
        "blocks": [_door_common(True)],
        "products": ["prehung_door", "entry_door"],
    },
    "screen_door": {
        "title": "Screen Door Order Form", "file": "OF-Screen Door.pdf",
        "header": dict(HEADER_STD), "sales_rep": "SalesRep",
        # The PDF has two widgets both named "Comments2" -> only two distinct lines
        "comments": {"pdf": ["Comments1", "Comments2"], "chars": [45, 55]},
        "blocks": [_screen_door_block(i) for i in range(4)],
        "products": ["screen_door"],
    },
    "vertical_blind": {
        "title": "Vertical Blind Order Form", "file": "OF-Vertical Blind.pdf",
        "header": dict(HEADER_STD), "sales_rep": "SalesRep",
        "comments": {"pdf": ["Comments1", "Comments2", "Comments3", "Comments4"], "chars": [40, 50, 50, 50]},
        "blocks": [_vert_block(i) for i in range(5)],
        "products": ["vertical_blind"],
    },
    "vertical_blind_2": {
        "title": "Vertical Blind Order Form (Inside/Outside Mount)", "file": "OrderFormVert.pdf",
        "header": {"name": "Customer Name", "acct": "Acct. #", "address": "Address", "city": "City",
                   "state": "State", "zip": "Zip Code", "mgmt": "Management Company", "phone": "Phone",
                   "po": "P.O", "date": "Date"},
        "sales_rep": "Sales Rep",
        "comments": {"pdf": ["Comment Line.0", "Comment Line.1", "Comment Line.2", "Comment Line.3"],
                     "chars": [50, 50, 50, 50]},
        "blocks": [_vert2_block(i) for i in range(5)],
        "products": ["vertical_blind"],
    },
    "window_screen": {
        "title": "Window Screen Order Form", "file": "OF-Window Screen.pdf",
        "header": dict(HEADER_STD), "sales_rep": "SalesRep",
        "comments": {"pdf": ["Comments1", "Comments2", "Comments3"], "chars": [45, 55, 55]},
        "blocks": [_window_screen_block(i) for i in range(3)],
        "products": ["window_screen"],
    },
    "horizontal_blind": {
        "title": "Horizontal Blind Order Form", "file": "OF-Horizontal Blind Order Form.pdf",
        "header": {"name": "Customer Name", "po": "PO Number", "date": "Date"},
        "sales_rep": None, "comments": None,
        "blocks": [_horiz_block(i) for i in range(3)],
        "products": ["horizontal_blind"],
    },
}

# "new_door" and "vertical_blind_2" are the company's alternate versions of the same paper forms.
# In the app they're merged into one Door form and one Vertical Blind form (see MERGED below);
# the app prints whichever paper version fits what was ordered.
FORM_ORDER = ["door", "prehung", "bypass", "screen_door", "window_screen", "horizontal_blind", "vertical_blind",
              "roller_shade", "trim", "door_hardware", "bath_hardware", "countertop", "cabinet_prefab",
              "cabinet_custom", "sliding_patio_door", "closet_bifold", "shower_door", "mirror_frame"]
RETIRED_FORMS = ["cabinet", "closet_shower"]   # still open old orders; no longer offered for new ones

# --------------------------------------------------------------------------
# Products serviced (for saved unit / floorplan measurements)
# --------------------------------------------------------------------------
GENERIC_FIELDS = [
    _t("qty", "Quantity", None, w=4),
    _t("item_no", "Item #", None),
    _t("width", "Width (in)", None, measure=True),
    _t("height", "Height (in)", None, measure=True),
    _t("length", "Length (in / linear ft)", None, measure=True),
    _t("depth", "Depth (in)", None, measure=True),
    _t("thickness", "Thickness", None),
    _t("style", "Style / Profile", None),
    _t("color", "Color", None),
    _t("finish", "Finish / Material", None),
    _t("hardware", "Hardware", None),
]

PRODUCTS = {
    "interior_door": {"label": "Interior Door", "forms": ["door", "new_door"]},
    "entry_door": {"label": "Entry / Exterior Door", "forms": ["door", "new_door", "prehung"]},
    "prehung_door": {"label": "Pre-Hung Door", "forms": ["prehung"]},
    "bypass_door": {"label": "Bi-Pass Closet Door", "forms": ["bypass", "closet_bifold"]},
    "sliding_patio_door": {"label": "Sliding Patio Door", "forms": ["sliding_patio_door"]},
    "closet_door": {"label": "Closet / Bi-Fold Door", "forms": ["closet_bifold", "bypass"]},
    "storage_door": {"label": "Storage / Utility Door", "forms": ["door", "new_door"]},
    "garage_door": {"label": "Garage Door", "forms": []},
    "screen_door": {"label": "Screen Door", "forms": ["screen_door"]},
    "window_screen": {"label": "Window Screen", "forms": ["window_screen"]},
    "horizontal_blind": {"label": "Horizontal Blind", "forms": ["horizontal_blind"]},
    "vertical_blind": {"label": "Vertical Blind", "forms": ["vertical_blind", "vertical_blind_2"]},
    "roller_shade": {"label": "Roller Shade", "forms": ["roller_shade"]},
    "baseboard": {"label": "Baseboard", "forms": ["trim"]},
    "casing": {"label": "Casing", "forms": ["trim"]},
    "door_hardware": {"label": "Door Hardware", "forms": ["door_hardware"]},
    "bath_hardware": {"label": "Bath Hardware", "forms": ["bath_hardware"]},
    "cabinet": {"label": "Cabinet (kitchen / vanity)", "forms": ["cabinet_prefab", "cabinet_custom"]},
    "cabinet_door": {"label": "Cabinet Door / Drawer Front", "forms": ["cabinet_custom", "cabinet_prefab"]},
    "countertop": {"label": "Countertop", "forms": ["countertop"]},
    "wardrobe_door": {"label": "Mirrored / Wardrobe Door", "forms": ["closet_bifold"]},
    "shower_door": {"label": "Shower Door / Enclosure", "forms": ["shower_door"]},
    "mirror_frame": {"label": "Mirror Frame (Mirror Mate)", "forms": ["mirror_frame"]},
    "other": {"label": "Other", "forms": []},
}


def _lwh_order(fields):
    """Standard L x W x H: length / width (and depth) always come before height.
    Only changes the on-screen order - the PDF is filled by field name, not position."""
    fields = list(fields)
    keys = [f["key"] for f in fields]
    for hk in [k for k in keys if k == "height" or k.endswith("_height")]:
        pre = hk[: -len("height")]
        partners = [pre + s for s in ("length", "width", "depth") if pre + s in keys]
        if not partners:
            continue
        hi = keys.index(hk)
        last = max(keys.index(k) for k in partners)
        if hi < last:
            f = fields.pop(hi)
            keys.pop(hi)
            last = max(keys.index(k) for k in partners)
            fields.insert(last + 1, f)
            keys.insert(last + 1, hk)
    # length before width when both exist
    if "length" in keys and "width" in keys and keys.index("length") > keys.index("width"):
        f = fields.pop(keys.index("length"))
        keys.remove("length")
        fields.insert(keys.index("width"), f)
        keys.insert(keys.index("width"), "length")
    return fields


# --------------------------------------------------------------------------
# App-only fields (no box on the paper form -> printed in the comments)
# --------------------------------------------------------------------------
def _mirror_frame_fields():
    show = ("mirror_frame", ["Yes"])
    return [
        _x("mirror_frame", "Mirror Frame", "choice", YN, photos="mirror_frame"),
        _x("mirror_length", "Mirror Frame Length (in)", measure=True, show_if=show),
        _x("mirror_height", "Mirror Frame Height (in)", measure=True, show_if=show),
        _x("mirror_finish", "Mirror Frame Finish", show_if=show),
    ]


def _door_hardware_fields():
    show = ("hardware", ["Yes"])
    return [
        _x("hardware", "Door Hardware", "choice", YN),
        _x("hw_collection", "Hardware Style", "choice", [(x, None) for x in HARDWARE_COLLECTIONS],
           photos="hardware_style", show_if=show),
        _x("hw_function", "Hardware Type", "choice", [(x, None) for x in ("Passage", "Privacy", "Entry")],
           show_if=show),
        _x("hw_finish", "Hardware Finish", "choice", [("Satin Nickel", None), ("Matte Black", None)], show_if=show),
    ]


def _prehung_fields():
    return [
        _x("prehung", "Pre-Hung", "choice", YN),
        _x("threshold", "Threshold Measurement (in)", measure=True, show_if=("prehung", ["Yes"])),
    ]


FIRE_RATINGS = ["20 min", "45 min", "60 min", "90 min"]


def _fire_fields(self_closing_box=True):
    """Fire-rated door: Yes/No, the rating, and (Door form) self-closing hinges - a rated door is a labeled
    assembly with self-closing hinges or a closer and a latch. Unit entry doors onto inside corridors are
    usually 20 min; stairwell / utility doors can be 45-90."""
    yes = ("fire_rated", ["Yes"])
    out = [_x("fire_rated", "Fire Rated", "choice", YN),
           _x("fire_rating", "Fire Rating", "choice", [(r, None) for r in FIRE_RATINGS], show_if=yes)]
    if self_closing_box:
        out.append(_x("self_closing", "Self-Closing Hinges", "choice", YN, show_if=yes))
    return out


def _fire_names(key):
    return {"fire_rated": lambda o: f"FireRated {_slug(o)}.0",
            "fire_rating": lambda o: f"FireRating {o.split()[0]}.0",
            "self_closing": lambda o: f"SelfClosing {_slug(o)}.0"}[key]


def _door_edge_field():
    """Beveled (standard, e.g. 29-3/4 x 79-1/2) or Full Book (square, e.g. 30 x 80). Printed in the line's comments."""
    from .door_edge import EDGES
    return _x("door_edge", "Door Edge", "choice", [(e, None) for e in EDGES], edge_hint=True)


def _after_height(block, extra):
    i = next((n for n, f in enumerate(block) if f["key"] == "height"), len(block) - 1)
    return block[:i + 1] + extra + block[i + 1:]


def _boxed(fields, names):
    """Give app-only fields their boxes on a company form that now has them (see form_patches)."""
    out = []
    for f in fields:
        f = {k: v for k, v in f.items() if k != "extra"}
        ref = names(f["key"])
        if f["kind"] == "choice":
            f["options"] = [(o[0], ref(o[0])) for o in f["options"]]
        else:
            f["pdf"] = ref(None)
        out.append(f)
    return out


def _door_names(key):
    group = {"hw_collection": "style", "hw_function": "type", "hw_finish": "finish"}.get(key)
    if group:
        return lambda o: f"Hardware {group} {_slug(o)}.0"
    return {"prehung": lambda o: f"PreHung {_slug(o)}.0", "hardware": lambda o: f"Hardware {_slug(o)}.0",
            "threshold": lambda o: "Threshold.0"}[key]


def _mirror_names(i):
    return lambda key: {"mirror_frame": lambda o: f"Mirror {_slug(o)}.{i}",
                        "mirror_length": lambda o: f"Mirror length.{i}",
                        "mirror_height": lambda o: f"Mirror height.{i}",
                        "mirror_finish": lambda o: f"Mirror finish.{i}"}[key]


FORMS["door"]["blocks"] = [b + _boxed(_prehung_fields() + _door_hardware_fields(), _door_names)
                           for b in FORMS["door"]["blocks"]]
FORMS["new_door"]["blocks"] = [b + _prehung_fields() + _door_hardware_fields() for b in FORMS["new_door"]["blocks"]]
FORMS["prehung"]["blocks"] = [b + _boxed(_door_hardware_fields(), _door_names) for b in FORMS["prehung"]["blocks"]]
FORMS["door"]["blocks"] = [b + _boxed(_fire_fields(), _fire_names) for b in FORMS["door"]["blocks"]]
FORMS["prehung"]["blocks"] = [b + _boxed(_fire_fields(self_closing_box=False), _fire_names)
                              for b in FORMS["prehung"]["blocks"]]


def _fire_after_location(block):
    """Show the fire-rated questions in the app right after Door Location (interior / exterior)."""
    fire = [f for f in block if f["key"] in ("fire_rated", "fire_rating") or
            (f["key"] == "self_closing" and f["kind"] == "choice")]
    rest = [f for f in block if f not in fire]
    i = next(n for n, f in enumerate(rest) if f["key"] == "door_location") + 1
    return rest[:i] + fire + rest[i:]


for _fk in ("door", "prehung"):
    FORMS[_fk]["blocks"] = [_fire_after_location(b) for b in FORMS[_fk]["blocks"]]
for _fk in ("door", "new_door", "prehung"):   # full book vs beveled edge (from the size)
    FORMS[_fk]["blocks"] = [_after_height(b, [_door_edge_field()]) for b in FORMS[_fk]["blocks"]]
# the New Door form has no comments box: print the edge under the Width box
FORMS["new_door"]["blocks"] = [[dict(f, **_ov(48, 670, 215, 9.5)) if f["key"] == "door_edge" else f for f in b]
                               for b in FORMS["new_door"]["blocks"]]
def _shift_overlays(fields, y_from, dy, y_to=10 ** 6):
    """Things the app writes at fixed spots (no box on the form) move with the form's lower half."""
    for f in fields:
        if f.get("overlay") and y_from < f["overlay"][1] < y_to:
            x, y, w = f["overlay"]
            f["overlay"] = (x, y + dy, w)
        for i, o in enumerate(f.get("options") or []):
            if isinstance(o[1], dict) and y_from < o[1]["mark"][1] < y_to:
                x, y = o[1]["mark"]
                f["options"][i] = (o[0], {"mark": (x, y + dy)})


def _door_shift():
    from .form_patches import DOOR_SPLIT, MEAS_BOT, door_added_height, lower_shift
    for fk in ("door", "prehung"):
        for b in FORMS[fk]["blocks"]:   # rows under the Measurements box moved a bit less than the ones above
            _shift_overlays(b, MEAS_BOT - 1, lower_shift())
            _shift_overlays(b, DOOR_SPLIT, door_added_height(), y_to=MEAS_BOT - 1 + lower_shift())


_door_shift()
FORMS["bypass"]["blocks"] = [b + _boxed(_mirror_frame_fields(), _mirror_names(i))
                             for i, b in enumerate(FORMS["bypass"]["blocks"])]


# --------------------------------------------------------------------------
# App-made forms (no official paper form; the app draws the PDF itself)
# --------------------------------------------------------------------------
def _g(key, label, kind="text", options=None, **kw):
    d = {"key": key, "label": label, "kind": kind, "pdf": None}
    if options is not None:
        d["options"] = options
    d.update(kw)
    return d


def _gc(key, label, names, **kw):
    return _g(key, label, "choice", [(x, None) for x in names], **kw)


_LINE_COMMENTS = _g("line_comments", "Line Comments", "lines", chars=[95])


def _hinge_cut_fields():
    """Concealed (cup) hinge bores on cabinet doors: yes/no, cup size, and where each hinge goes - top hinge from the
    top of the door to the hinge center, bottom hinge from the bottom of the door to its center, a center hinge (if
    any) from the top of the door to its center."""
    yes = ("hinge_cut", ["Yes"])
    return [_gc("hinge_cut", "Concealed Hinge Bores", ["Yes", "No"]),
            _gc("hinge_cut_dia", "Cup Diameter", ['35mm', '26mm', '40mm'], show_if=yes),
            _g("hinge_top", "Top & Bottom Hinge: Door Edge to Center (in)", measure=True, show_if=yes, placeholder="e.g. 3",
               pdf_label="Top Hinge from Top"),
            _g("hinge_bottom", "Bottom Hinge (fills in from the top - change only if different)", measure=True, show_if=yes,
               placeholder="e.g. 3", pdf_label="Btm Hinge from Btm"),
            _g("hinge_center", "Center Hinge: Top of Door to Center (in)", measure=True, show_if=yes,
               placeholder="only if there's a 3rd hinge", pdf_label="Ctr Hinge from Top")]

# Roller shade / sun screen fabrics - what AIS has invoiced (company sales history 2023-2026)
SHADE_BRANDS = ["Vertilux (VX)", "Mermet", "Phifer (Sheerweave / Suntex)", "Coolaroo", "Other"]
SHADE_FABRICS = ["VX Solar Screen 3000", "VX Solar 3101 (1%)", "VX Solar 3103 (3%)", "VX Solar 3105 (5%)",
                 "VX Screen 3000 Blackout", "VX Nightfall Blackout", "VX Colorama (Solar 3000)", "VX Shantung",
                 "Manchester LF (York Shadow / Burlap)", "Avilla Twilight Blackout", "Zora Twilight Blackout",
                 "Mermet E-Screen", "Sheerweave 2410", "Sheerweave 7100 Blackout", "Capri Zebra",
                 "Coolaroo exterior (Montecito / Outback)", "Phifer Suntex 97 exterior"]
SHADE_OPENNESS = ["1% (most privacy)", "3%", "5%", "10% (most view-through)", "Blackout (0%)",
                  "Light filtering"]
SHADE_COLORS = ["White Pearl", "White/Linen", "Linen", "Ebony", "Grey", "Charcoal/Grey", "Taupe", "Ivory",
                "Stone", "Coffee"]

GENERATED = {
    "roller_shade": {   # mimics the company's Blind Order Form (Taylor's request)
        "title": "Roller Shade Order Form", "style": "blind", "form_title": "ROLLER SHADE ORDER FORM",
        "blocks": [[
            _g("qty", "Quantity", w=4), _g("unit", "Unit #"), _g("room", "Location (room)"),
            _g("width", "Window Width (in)", measure=True), _g("length", "Window Length (in)", measure=True),
            _gc("chain_side", "Chain Side", ["Left", "Right"]),
            # material / opacity / brand - lists come from the roller shades AIS has sold
            _g("shade_brand", "Brand", suggest=SHADE_BRANDS, placeholder="Vertilux, Mermet..."),
            _g("shade_fabric", "Material / Fabric", suggest=SHADE_FABRICS, placeholder="e.g. VX Solar Screen 3000"),
            _g("openness", "Opacity (Openness)", suggest=SHADE_OPENNESS, placeholder="1%, 3%, 5%, Blackout..."),
            _gc("color", "Color", ["Champagne", "Tan", "White", "Other"], other_option="Other",
                other_field="color_other"),
            _g("color_other", "Color - Other", show_if=("color", ["Other"]), suggest=SHADE_COLORS),
            _LINE_COMMENTS]],
        "products": ["roller_shade"],
    },
    "trim": {   # same look as the company's Door / Bi-Pass forms
        "title": "Baseboard & Casing Order Form", "style": "door", "section": "Trim Information",
        "blocks": [[
            _g("unit", "Unit #"), _g("room", "Location (room)"),
            _gc("trim_type", "Casing or Baseboard", ["Casing", "Baseboard"]),
            _gc("profile", "Type", [f"{k}: {d}" for k, d in TRIM_PROFILES], photos="trim_profile"),
            _gc("flat_thick", "Flat Trim Thickness", FLAT_TRIM_THICK, show_if=("profile", [FLAT_TRIM_PROFILE])),
            _gc("flat_height", "Flat Trim Height", FLAT_TRIM_HEIGHT, show_if=("profile", [FLAT_TRIM_PROFILE])),
            _g("flat_size", "Exact Size (if different)", show_if=("profile", [FLAT_TRIM_PROFILE]),
               placeholder='e.g. 5/8" x 4-3/4"'),
            _g("length", "Linear Feet Needed", measure=True),
            _LINE_COMMENTS]],
        "products": ["baseboard", "casing"],
    },
    "door_hardware": {   # same look as the company's Door / Bi-Pass forms
        "title": "Door Hardware Order Form", "style": "door", "section": "Hardware Information",
        "blocks": [[
            _g("qty", "Quantity", w=4), _g("unit", "Unit #"), _g("room", "Location (room)"),
            _gc("hw_collection", "Hardware Style", HARDWARE_COLLECTIONS, photos="hardware_style"),
            _gc("hw_function", "Hardware Type", ["Passage", "Privacy", "Entry"]),
            _gc("hw_finish", "Finish", ["Satin Nickel", "Matte Black"]),
            _LINE_COMMENTS]],
        "products": ["door_hardware"],
    },
    "bath_hardware": {   # same look as the company's Door / Bi-Pass forms
        "title": "Bath Hardware Order Form", "style": "door", "section": "Hardware Information",
        "blocks": [[
            _g("qty", "Quantity", w=4), _g("unit", "Unit #"), _g("room", "Location (room)"),
            _gc("bath_collection", "Collection", [f"{n} ({fin})" for n, fin in BATH_COLLECTIONS],
                photos="bath_collection"),
            _gc("bath_item", "Item", ["Towel Ring", "Paper Holder", "Towel Bar"]),
            _LINE_COMMENTS]],
        "products": ["bath_hardware"],
    },
    "cabinet": {   # same look as the company's Door / Bi-Pass forms
        "title": "Cabinet & Countertop Order Form", "style": "door", "section": "Cabinet Information",
        "blocks": [[
            _g("qty", "Quantity", w=4), _g("unit", "Unit #"),
            _gc("cab_room", "Cabinet Type", ["Kitchen", "Bathroom"]),
            _gc("cab_item", "Item", ["Doors", "Single Box", "Double Box", "Double Box with Sink",
                                     "Extended Double Box with Sink"]),
            _gc("style", "Door Style", CABINET_STYLES, photos="cabinet_style"),
            _g("width", "Width (in)", measure=True), _g("depth", "Depth (in)", measure=True),
            _g("height", "Height (in)", measure=True),
            _gc("drawers", "Drawers Included", ["Yes", "No"]),
            *_hinge_cut_fields(),
            _g("cab_hardware", "Cabinet Hardware *", placeholder="* hardware list coming soon - type it in"),
            _gc("refacing", "Refacing (Thermofoil)", ["Yes", "No"]),
            _g("thermofoil_color", "Thermofoil Color", show_if=("refacing", ["Yes"])),
            _g("countertop", "Countertop *", placeholder="* countertop samples coming soon - type it in"),
            ] + _mirror_frame_fields() + [_LINE_COMMENTS]],
        "products": ["cabinet", "cabinet_door", "countertop"],
    },
    "closet_shower": {   # same look as the company's Door / Bi-Pass forms
        "title": "Wardrobe, Closet & Shower Door Order Form", "style": "door", "section": "Door Information",
        "blocks": [[
            _g("qty", "Quantity", w=4), _g("unit", "Unit #"), _g("room", "Location (room)"),
            _gc("closet_type", "Door Type", ["Mirrored Swing Door", "Mirrored Bypass Door",
                                             "White Vinyl Closet Door", "Shower Door / Enclosure"],
                photos="closet_type"),
            _gc("finish", "Frame Finish", ["White", "Satin Nickel", "Aztec Gold", "Other"],
                other_option="Other", other_field="finish_other",
                show_if=("closet_type", ["Mirrored Swing Door", "Mirrored Bypass Door"])),
            _g("finish_other", "Finish - Other", show_if=("finish", ["Other"])),
            _g("width", "Opening Width (in)", measure=True), _g("height", "Opening Height (in)", measure=True),
            _g("shower_style", "Shower Style / Custom Details",
               placeholder="Many styles and custom options - describe it",
               show_if=("closet_type", ["Shower Door / Enclosure"])),
            ] + _mirror_frame_fields() + [_LINE_COMMENTS]],
        "products": ["closet_door", "wardrobe_door", "shower_door"],
    },
    "countertop": {   # same look as the company's Door / Bi-Pass forms
        "title": "Countertop Order Form", "style": "door", "section": "Countertop Information",
        "blocks": [[
            _g("qty", "Quantity", w=4), _g("unit", "Unit #"),
            _gc("ct_room", "Room", ["Kitchen", "Bathroom Vanity"]),
            _gc("ct_material", "Material", COUNTERTOP_MATERIALS),
            _gc("ct_layout", "Layout", ["Straight", "L-Shape", "U-Shape", "Island"]),
            _g("length", "Run A Length (in)", measure=True),
            _g("length_b", "Run B Length (in)", measure=True, show_if=("ct_layout", ["L-Shape", "U-Shape"])),
            _g("length_c", "Run C Length (in)", measure=True, show_if=("ct_layout", ["U-Shape"])),
            _g("depth", "Depth (in)", measure=True),
            _gc("backsplash", "Backsplash", ["Yes", "No"]),
            _gc("end_splash", "End Splash", ["None", "Left", "Right", "Both"]),
            _gc("sink", "Sink Cutout", ["None", "Single Bowl", "Double Bowl", "Vanity Bowl"]),
            _g("sink_center", "Sink Center From Left End (in)", measure=True,
               show_if=("sink", ["Single Bowl", "Double Bowl", "Vanity Bowl"])),
            _g("ct_color", "Color / Edge *", placeholder="* countertop samples coming soon - type it in"),
            _LINE_COMMENTS]],
        "products": ["countertop"],
    },
    "cabinet_prefab": {   # same look as the company's Door / Bi-Pass forms
        "title": "Pre-Fab Cabinet Order Form", "style": "door", "section": "Cabinet Information",
        "blocks": [[
            _g("qty", "Quantity", w=4), _g("unit", "Unit #"),
            _gc("cab_room", "Cabinet Type", ["Kitchen", "Bathroom Vanity"]),
            _gc("cab_item", "Item", PREFAB_KITCHEN_ITEMS + PREFAB_VANITY_ITEMS),
            _gc("style", "Door Style", CABINET_STYLES, photos="cabinet_style"),
            _g("width", "Width (in)", measure=True), _g("depth", "Depth (in)", measure=True),
            _g("height", "Height (in)", measure=True),
            _gc("hinge_side", "Door Hinge Side", ["Left", "Right", "Pair"]),
            _gc("drawers", "Drawers Included", ["Yes", "No"]),
            *_hinge_cut_fields(),
            _g("cab_hardware", "Cabinet Hardware *", placeholder="* hardware list coming soon - type it in"),
            _LINE_COMMENTS]],
        "products": ["cabinet", "cabinet_door"],
    },
    "cabinet_custom": {   # same look as the company's Door / Bi-Pass forms
        "title": "Custom Cabinet Order Form", "style": "door", "section": "Cabinet Information",
        "blocks": [[
            _g("qty", "Quantity", w=4), _g("unit", "Unit #"),
            _gc("cab_room", "Cabinet Type", ["Kitchen", "Bathroom Vanity"]),
            _gc("cab_kind", "Cabinet", ["Base", "Sink Base", "Wall", "Tall / Pantry", "Vanity"]),
            _gc("style", "Door Style", CABINET_STYLES + ["Other"], photos="cabinet_style",
                other_option="Other", other_field="style_other"),
            _g("style_other", "Style - Other", show_if=("style", ["Other"])),
            _g("cab_finish", "Finish / Color"),
            _g("width", "Width (in)", measure=True), _g("depth", "Depth (in)", measure=True),
            _g("height", "Height (in)", measure=True),
            _g("doors_qty", "Number of Doors", w=4), _g("drawers_qty", "Number of Drawers", w=4),
            _gc("hinge_side", "Door Hinge Side", ["Left", "Right", "Pair"]),
            *_hinge_cut_fields(),
            _gc("refacing", "Refacing (Thermofoil)", ["Yes", "No"]),
            _g("thermofoil_color", "Thermofoil Color", show_if=("refacing", ["Yes"])),
            _g("cab_hardware", "Cabinet Hardware *", placeholder="* hardware list coming soon - type it in"),
            _LINE_COMMENTS]],
        "products": ["cabinet", "cabinet_door"],
    },
    "sliding_patio_door": {   # same look as the company's Door / Bi-Pass forms
        "title": "Sliding Patio Door Order Form", "style": "door", "section": "Patio Door Information",
        "blocks": [[
            _g("qty", "Quantity", w=4), _g("unit", "Unit #"),
            _g("width", "Opening Width (in)", measure=True), _g("height", "Opening Height (in)", measure=True),
            _gc("config", "Panel That Slides (from outside)", ["XO - Left Slides", "OX - Right Slides"]),
            _gc("frame", "Frame", ["Vinyl", "Aluminum"]),
            _gc("frame_color", "Frame Color", ["White", "Tan / Almond", "Bronze", "Other"],
                other_option="Other", other_field="frame_color_other"),
            _g("frame_color_other", "Frame Color - Other", show_if=("frame_color", ["Other"])),
            _gc("glass", "Glass", ["Clear", "Low-E"]),
            _gc("grids", "Grids", ["Yes", "No"]),
            _gc("screen", "Screen Door Included", ["Yes", "No"]),
            _LINE_COMMENTS]],
        "products": ["sliding_patio_door"],
    },
    "closet_bifold": {   # same look as the company's Door / Bi-Pass forms
        "title": "Wardrobe, Closet & Bi-Fold Door Order Form", "style": "door", "section": "Closet Door Information",
        "blocks": [[
            _g("qty", "Quantity", w=4), _g("unit", "Unit #"), _g("room", "Location (room)"),
            _gc("closet_type", "Door Type", CLOSET_TYPES + WARDROBE_TYPES, photos="closet_type"),
            _gc("frame_finish", "Mirror Frame Finish", WARDROBE_FINISHES, other_option="Other",
                other_field="frame_finish_other", show_if=("closet_type", WARDROBE_TYPES[:2])),
            _g("frame_finish_other", "Mirror Frame Finish - Other", show_if=("frame_finish", ["Other"])),
            _gc("style", "Door Style", ["Smooth", "Shaker", "Colonist", "Camden", "Carrara", "Riverside", "Mirrored",
                                        "Other"], other_option="Other", other_field="style_other",
                photos="door_style"),
            _g("style_other", "Style - Other", show_if=("style", ["Other"])),
            _gc("panels", "Panels (design)", ["1 Panel", "2 Panel", "3 Panel", "4 Panel", "5 Panel", "6 Panel", "Flush"]),
            _gc("leaves", "Number of Doors (leaves)", ["1", "2", "3", "4"]),
            _gc("finish", "Finish", ["Primecoat", "Embossed Primecoat", "White", "Other"], other_option="Other",
                other_field="finish_other"),
            _g("finish_other", "Finish - Other", show_if=("finish", ["Other"])),
            _gc("core", "Core Type", ["Hollow Core", "Solid Core"]),
            _gc("thickness", "Door Thickness", ['1-3/8"', '1-1/8"', "Other"]),
            _g("width", "Door Width (in)", measure=True, placeholder="each door"),
            _g("height", "Door Height (in)", measure=True),
            _g("opening_width", "Opening W (in)", measure=True),
            _g("opening_height", "Opening H (in)", measure=True),
            _gc("ball_catch", "Ball Catch", ["Yes", "No"]),
            _gc("finger_pull", "Finger Pull", ["Yes", "No"]),
            _g("finger_pull_dist", "Finger Pull from Top (in)", measure=True, show_if=("finger_pull", ["Yes"])),
            _gc("track", "Track & Hardware Included", ["Yes", "No"]),
            _LINE_COMMENTS]],
        "products": ["closet_door", "bypass_door", "wardrobe_door"],
    },
    "shower_door": {   # same look as the company's Door / Bi-Pass forms
        "title": "Shower Door & Enclosure Order Form", "style": "door", "section": "Shower Door Information",
        "blocks": [[
            _g("qty", "Quantity", w=4), _g("unit", "Unit #"), _g("room", "Location (room)"),
            _gc("shower_location", "Goes On", ["Tub", "Shower stall"]),
            _gc("shower_type", "Type", SHOWER_TYPES),
            _gc("shower_frame", "Frame", SHOWER_FRAMES),
            _gc("finish", "Hardware / Frame Finish", SHOWER_FINISHES, other_option="Other",
                other_field="finish_other"),
            _g("finish_other", "Finish - Other", show_if=("finish", ["Other"])),
            _gc("glass", "Glass", SHOWER_GLASS, other_option="Other", other_field="glass_other"),
            _g("glass_other", "Glass - Other", show_if=("glass", ["Other"])),
            _g("width", "Opening Width (in)", measure=True), _g("height", "Opening Height (in)", measure=True),
            _g("return_depth", "Return Panel Depth (in)", measure=True,
               show_if=("shower_type", ["Enclosure (door + panels)"])),
            _gc("door_hinge", "Door Opens / Hinges On", ["Left", "Right"],
                show_if=("shower_type", ["Swing / pivot door", "Enclosure (door + panels)"])),
            _g("shower_style", "Style / Custom Details", placeholder="Many styles and custom options - describe it"),
            _LINE_COMMENTS]],
        "products": ["shower_door"],
    },
    "mirror_frame": {   # Mirror Mate frame kits
        "title": "Mirror Frame (Mirror Mate) Order Form", "style": "door", "section": "Mirror Frame Information",
        "blocks": [[
            _g("qty", "Quantity", w=4), _g("unit", "Unit #"), _g("room", "Location (room)"),
            _g("mirror_length", "Mirror Width (in)", measure=True),
            _g("mirror_height", "Mirror Height (in)", measure=True),
            _gc("mirror_mount", "Mirror Is", ["Clipped to wall", "Glued to wall", "Medicine cabinet", "Other"]),
            _g("mirror_finish", "Frame Finish (70+ finishes)", placeholder="Finish name or number"),
            _LINE_COMMENTS]],
        "products": ["mirror_frame"],
    },
}
GENERATED_FILES = {"roller_shade": "OF-Roller Shade.pdf", "trim": "OF-Baseboard and Casing.pdf",
                   "door_hardware": "OF-Door Hardware.pdf", "bath_hardware": "OF-Bath Hardware.pdf",
                   "cabinet": "OF-Cabinet and Countertop.pdf", "closet_shower": "OF-Closet and Shower Door.pdf",
                   "countertop": "OF-Countertop.pdf", "cabinet_prefab": "OF-Pre-Fab Cabinet.pdf",
                   "cabinet_custom": "OF-Custom Cabinet.pdf", "sliding_patio_door": "OF-Sliding Patio Door.pdf",
                   "closet_bifold": "OF-Closet and Bi-Fold Door.pdf", "shower_door": "OF-Shower Door.pdf",
                   "mirror_frame": "OF-Mirror Frame.pdf"}


def _bind(fields, n):
    """Give an app-made form's fields their widget names for line n (see form_builder)."""
    from .form_builder import cname, tname
    out = []
    for f in fields:
        g = {k: v for k, v in f.items() if k != "extra"}
        if f["kind"] == "text":
            g["pdf"] = tname(n, f["key"])
        elif f["kind"] == "lines":
            g["pdf"] = [tname(n, f["key"])]
        elif f["kind"] == "choice":
            g["options"] = [(o[0], cname(n, f["key"], o[0])) for o in f["options"]]
        out.append(g)
    return out


def _app_made(spec, fk):
    from .form_builder import door_per_page, per_page
    fields = _lwh_order(spec["blocks"][0])
    if spec.get("style") == "door":   # built on the company Bi-Pass form: its header / comments / rep boxes
        n = door_per_page(fields)
        extra = dict(sales_rep="SR", comments={"pdf": ["Comment1", "Comments2", "Comments3"], "chars": [45, 55, 55]})
    else:
        n = per_page(fields)
        extra = dict(sales_rep="Sales Rep", comments={"pdf": ["Comments1", "Comments2", "Comments3"],
                                                      "chars": [55, 55, 55]})
    return dict(spec, app_made=True, file=GENERATED_FILES[fk], header=dict(HEADER_STD),
                blocks=[_bind(fields, i) for i in range(n)], **extra)


for _fk, _spec in GENERATED.items():
    FORMS[_fk] = _app_made(_spec, _fk)

for _fk in FORMS:
    FORMS[_fk]["blocks"] = [_lwh_order(b) for b in FORMS[_fk]["blocks"]]
GENERIC_FIELDS = _lwh_order(GENERIC_FIELDS)


# --------------------------------------------------------------------------
# Merged forms: one choice in the app, two paper versions behind it
# --------------------------------------------------------------------------
DOOR_STYLES = [  # app label -> label on each paper form (None = not on that form; "Other" = written in)
    ("6 Panel (Colonist)", {"door": "Six-Panel Embossed (Colonist)", "new_door": "Colonist (6-Panel)"}),
    ("5 Panel (Riverside)", {"door": "Other", "new_door": "Riverside (5-Panel)"}),
    ("2 Panel (Carrera)", {"door": "Other", "new_door": "Carrera (2-Panel)"}),
    ("2 Panel Arch", {"door": "Other", "new_door": None}),
    ("2 Panel Arch Plank", {"door": "Other", "new_door": None}),
    ("5 Panel Shaker", {"door": "Other", "new_door": None}),
    ("3 Panel Shaker Equal", {"door": "Other", "new_door": None}),
    ("3 Panel Shaker Craftsman", {"door": "Other", "new_door": None}),
    ("2 Panel Shaker", {"door": "Other", "new_door": None}),
    ("1 Panel Shaker", {"door": "Other", "new_door": None}),
    ("HC Primecoat", {"door": "Other", "new_door": None}),
    ("Other", {"door": "Other", "new_door": None}),
]
DOOR_FINISHES = [
    ("Primecoat", {"door": "Primecoat", "new_door": "Primecoat"}),
    ("Embossed Primecoat", {"door": "Embossed Primecoat", "new_door": "Embossed Primecoat"}),
    ("Oak Legacy", {"door": "Oak Legacy", "new_door": "Oak Legacy"}),
    ("Walnut Legacy", {"door": "Walnut Legacy", "new_door": None}),
    ("Other", {"door": None, "new_door": "Other"}),
]
MERGED = {
    "vertical_blind": {"variants": ["vertical_blind", "vertical_blind_2"], "default": "vertical_blind"},
}
VARIANT_PARENT = {v: k for k, m in MERGED.items() for v in m["variants"] if v != k}


def _choice(key, label, table, other_field=None, **kw):
    d = {"key": key, "label": label, "kind": "choice", "options": [(u, None) for u, _ in table],
         "other_option": "Other" if any(u == "Other" for u, _ in table) else None, "other_field": other_field}
    d.update(kw)
    return d


def editor_fields(form_key):
    """Fields shown in the app for a form. Merged forms show everything both paper versions can take."""
    if form_key == "door":   # door type first, then finish
        out = list(FORMS["door"]["blocks"][0])
        picked = [next(x for x in out if x["key"] == k) for k in ("style", "style_other", "finish")]
        picked[0] = dict(picked[0], label="Door Type")
        picked[1] = dict(picked[1], label="Other type", show_if=("style", ["Other"]))
        rest = [x for x in out if x["key"] not in ("style", "style_other", "finish")]
        i = next(i for i, x in enumerate(rest) if x["key"] not in ("qty", "item_no"))
        return rest[:i] + picked + rest[i:]
    if form_key == "vertical_blind":
        base = list(FORMS["vertical_blind"]["blocks"][0])
        mount = next(x for x in FORMS["vertical_blind_2"]["blocks"][0] if x["key"] == "mount")
        i = [x["key"] for x in base].index("room") + 1
        return base[:i] + [mount] + base[i:]
    return FORMS[form_key]["blocks"][0]


def choose_variant(form_key, data):
    """Which paper form to print for a merged form, based on what was ordered."""
    m = MERGED.get(form_key)
    if not m:
        return form_key
    blocks = [b for b in (data.get("blocks") or []) if any(v for v in b.values())]
    if form_key == "door":
        return "door"   # one Door Order Form; types without a box are written in its "Other" box
    if form_key == "vertical_blind":
        if any(b.get(k) for b in blocks for k in ("valance", "val_only", "item_no")):
            return "vertical_blind"
        if any(b.get("mount") for b in blocks):
            return "vertical_blind_2"
    return m["default"]


def translate_for_variant(form_key, variant, data):
    """Re-label one merged order's values for the paper form being printed."""
    import copy
    d = copy.deepcopy(data)
    notes = []
    keys = {f["key"] for f in FORMS[variant]["blocks"][0]}
    for i, b in enumerate(d.get("blocks") or [], 1):
        if form_key == "door":
            for table, key, other in ((DOOR_STYLES, "style", "style_other"), (DOOR_FINISHES, "finish", "finish_other")):
                val = b.get(key)
                if not val:
                    continue
                u = match_option(val, [(x, x) for x, _ in table])
                target = dict(table)[u][variant] if u else None
                written = b.get(other) if u == "Other" else u   # what to write in by hand
                if target and target != "Other":
                    b[key] = target
                elif target == "Other" or u == "Other":
                    if other in keys:
                        b[key] = "Other"
                        if written:
                            b[other] = written
                    else:   # this paper form has no "Other" box: write it out
                        b[key] = written or val
                else:
                    b[key] = val
        if form_key == "vertical_blind" and variant == "vertical_blind" and b.get("mount"):
            notes.append(f"Line {i}: {b['mount']}")
        for k in list(b):
            if k not in keys and k not in ("unit", "room", "_room", "product") and b.get(k):
                if k in ("style_other", "finish_other") and variant == "new_door" and k == "style_other":
                    notes.append(f"Line {i} style: {b[k]}")
                b.pop(k, None)
    if notes:
        d["comments"] = "; ".join([x for x in [d.get("comments", "")] if x] + notes)
    return d


def split_door_style(blk):
    """Old saved doors ('2 Panel Arch') -> style 'Arch' + panels '2 Panel' + top 'Arched Top'."""
    if isinstance(blk, dict) and blk.get("style") in OLD_DOOR_STYLES:
        st, pn, tp = OLD_DOOR_STYLES[blk["style"]]
        blk = dict(blk, style=st)
        if pn and not blk.get("panels"):
            blk["panels"] = pn
        if tp and not blk.get("door_top"):
            blk["door_top"] = tp
        if not st:
            blk.pop("style")
    return blk


_OLD_CLOSET_STYLES = {"Flat (smooth)": ("Smooth", "Flush"), "2 Panel": ("", "2 Panel"), "6 Panel": ("Colonist", "6 Panel")}


def split_closet(blk):
    """Closet doors saved before v2.12: 'Number of Panels' (2/4) meant how many doors; style held panels."""
    if not isinstance(blk, dict):
        return blk
    blk = dict(blk)
    if blk.get("panels") in ("2", "4") and not blk.get("leaves"):
        blk["leaves"] = blk.pop("panels")
    if blk.get("style") in _OLD_CLOSET_STYLES:
        st, pn = _OLD_CLOSET_STYLES[blk["style"]]
        if st:
            blk["style"] = st
        else:
            blk.pop("style")
        blk.setdefault("panels", pn)
    return blk


def door_photo(blk):
    """Photo for a door: the exact panels + style picture if there is one ('2 Panel' + 'Arch' -> 2-panel-arch),
    else the style, else the panel count."""
    from . import option_photos
    st, pn = (blk.get("style") or "").strip(), (blk.get("panels") or "").strip()
    cands = []
    if pn == "Flush":
        cands.append("HC Primecoat")
    if pn and st:
        cands.append(f"{pn} {st}")
    if pn and (blk.get("door_top") or "").startswith("Arch") and "arch" not in st.lower():
        cands.append(f"{pn} Arch")     # Panel Top = Arched Top
    cands += [c for c in (st, pn) if c]
    for c in cands:
        p = option_photos.find("door_style", c)
        if p:
            return p
    return None


def split_blind_style(blk):
    """Old saved horizontal blinds ('2" Metal Plus') -> style 'Metal Plus' + slat size '2"'."""
    if isinstance(blk, dict) and blk.get("style") in OLD_BLIND_STYLES:
        st, sl = OLD_BLIND_STYLES[blk["style"]]
        blk = dict(blk, style=st)
        blk.setdefault("slat_size", sl)
        if not blk.get("slat_size"):
            blk["slat_size"] = sl
    return blk


def product_fields(product_key):
    """Union of the block fields of every form for that product, so a saved
    measurement can fill any of them. Choices become suggestion lists so custom
    values are still allowed."""
    forms = PRODUCTS.get(product_key, {}).get("forms", [])
    if not forms:
        return deepcopy(GENERIC_FIELDS)
    out, seen = [], set()
    for fk in forms:
        for f in FORMS[fk]["blocks"][0]:
            if f["key"] in seen or f["key"] in ("unit", "line_comments", "price"):
                continue
            seen.add(f["key"])
            g = {"key": f["key"], "label": f["label"], "kind": "text",
                 "measure": f.get("measure", False)}
            if f["kind"] == "choice":
                g["suggest"] = [o[0] for o in f["options"]]
                # merge options from other forms with the same key
                for fk2 in forms:
                    for f2 in FORMS[fk2]["blocks"][0]:
                        if f2["key"] == f["key"] and f2["kind"] == "choice":
                            for o in f2["options"]:
                                if o[0] not in g["suggest"]:
                                    g["suggest"].append(o[0])
            elif f["kind"] == "bool":
                g["suggest"] = ["Yes", "No"]
            elif f.get("suggest"):
                g["suggest"] = list(f["suggest"])
            out.append(g)
    return out


def forms_for_product(product_key):
    return PRODUCTS.get(product_key, {}).get("forms", [])


def products_for_form(form_key):
    return [p for p, v in PRODUCTS.items() if form_key in v["forms"]]


def block_fields(form_key):
    return FORMS[form_key]["blocks"][0]


def form_list():
    return [(k, FORMS[k]["title"]) for k in FORM_ORDER]


def public_spec(form_key):
    """JSON-safe description of a form for the browser UI."""
    f = FORMS[form_key]
    fields = []
    for fd in editor_fields(form_key):
        d = {"key": fd["key"], "label": fd["label"], "kind": fd["kind"]}
        if fd["kind"] == "choice":
            d["options"] = [o[0] for o in fd["options"]]
        if fd.get("suggest"):
            d["suggest"] = fd["suggest"]
        if fd.get("measure"):
            d["measure"] = True
        if fd.get("w"):
            d["w"] = fd["w"]
        for k in ("placeholder", "extra"):
            if fd.get(k):
                d[k] = fd[k]
        if fd.get("show_if"):
            d["show_if"] = {"key": fd["show_if"][0], "in": list(fd["show_if"][1])}
        if fd.get("photos") and fd["kind"] == "choice":
            from . import option_photos as photos
            d["photos"] = fd["photos"]
            d["photo_map"] = {o: photos.slug(o) for o in d["options"] if photos.find(fd["photos"], o)}
        fields.append(d)
    door_slugs = []
    if any(fd.get("photos") == "door_style" for fd in fields):
        from . import option_photos as photos
        door_slugs = photos.available("door_style")
    return {
        "key": form_key, "title": f["title"], "door_photo_slugs": door_slugs,
        "header": [k for k in ["name", "acct", "address", "city", "state", "zip", "mgmt", "phone", "po", "date"]
                   if k in f["header"] or (k in ("city", "state", "zip") and f.get("address_combined"))],
        "has_sales_rep": bool(f.get("sales_rep")),
        "has_comments": bool(f.get("comments")),
        "blocks_per_page": len(f["blocks"]),
        "fields": fields,
        "has_unit_field": any(x["key"] == "unit" for x in f["blocks"][0]),
        "merged": form_key in MERGED,
    }


# --------------------------------------------------------------------------
# Option matching (custom / cross-form values)
# --------------------------------------------------------------------------
def _norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def match_option(value, options):
    """Return the option label that best matches value, or None."""
    if value in (None, ""):
        return None
    v = _norm(value)
    labels = [o[0] if isinstance(o, (list, tuple)) else o for o in options]
    for lab in labels:
        if _norm(lab) == v:
            return lab
    # numeric-ish shorthand: 3.5 == 3-1/2, 1 3/8 == 1-3/8
    alias = v.replace("35", "312").replace("15", "112")
    for lab in labels:
        if _norm(lab) == alias:
            return lab
    # token containment, e.g. "Colonist" matches "Six-Panel Embossed (Colonist)"
    if len(v) >= 3:
        hits = [lab for lab in labels if v in _norm(lab) or (_norm(lab) in v and len(_norm(lab)) >= 3)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            hits.sort(key=lambda h: len(_norm(h)))
            return hits[0]
    words = [w for w in str(value).lower().replace("(", " ").replace(")", " ").split() if len(w) > 3]
    for lab in labels:
        ll = lab.lower()
        if any(w in ll for w in words):
            return lab
    return None
