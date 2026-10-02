"""Purchase history: every line item from a customer's past orders, flattened for searching."""
from .catalog import FORMS, PRODUCTS
from .db import loads, q

META = {"unit", "room", "_room", "product", "_product", "description", "qty", "item_no", "price", "amount",
        "width", "height", "length", "depth", "color", "notes", "line_comments"}


def _form_product(fk):
    for k, p in PRODUCTS.items():
        if fk and fk in p["forms"]:
            return k
    return None


def lines(customer_id=None, search="", limit=2000):
    sql = """SELECT o.*, c.name AS cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
             WHERE o.status <> 'Cancelled'"""
    args = []
    if customer_id:
        sql += " AND o.customer_id=?"
        args.append(customer_id)
    sql += " ORDER BY coalesce(o.order_date, o.created) DESC, o.id DESC"
    out = []
    terms = [t for t in (search or "").lower().split() if t]
    for o in q(sql, args):
        data = loads(o["data"]) or {}
        fallback = _form_product(o["form_key"])
        for i, b in enumerate(data.get("blocks") or []):
            if not isinstance(b, dict) or not any(v for v in b.values()):
                continue
            prod = b.get("product") or fallback or "other"
            size = " x ".join(str(b[k]) for k in ("width", "height") if b.get(k))
            if b.get("length"):
                size = (size + " · " if size else "") + f"L {b['length']}"
            details = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in b.items() if k not in META and v)
            row = {"order_id": o["id"], "idx": i, "customer_id": o["customer_id"], "cname": o["cname"] or "",
                   "date": (o["order_date"] or (o["created"] or "")[:10]), "po": o["po"] or "",
                   "status": o["status"], "source": o["source"], "unit": b.get("unit", ""),
                   "room": b.get("room") or b.get("_room") or "", "product": prod,
                   "product_label": PRODUCTS.get(prod, {}).get("label", prod), "description": b.get("description", ""),
                   "qty": b.get("qty", ""), "size": size, "color": b.get("color", ""), "details": details,
                   "price": b.get("price", ""), "amount": b.get("amount", ""), "notes": b.get("notes", "")}
            if terms:
                hay = " ".join(str(v) for v in row.values()).lower()
                if not all(t in hay for t in terms):
                    continue
            out.append(row)
            if len(out) >= limit:
                return out
    return out


def block(order_id, idx):
    o = q("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not o:
        return None, None
    blocks = (loads(o["data"]) or {}).get("blocks") or []
    return o, (blocks[idx] if 0 <= idx < len(blocks) else None)
