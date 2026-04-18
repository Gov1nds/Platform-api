"""OCR quote parsing from Textract output (Blueprint §12.4)."""
from decimal import Decimal, InvalidOperation
import re

_CURRENCY_MAP = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "₩": "KRW"}

def _to_decimal(s):
    try: return Decimal(re.sub(r"[^\d.-]", "", s or "") or "0")
    except InvalidOperation: return Decimal("0")

def _detect_currency(fields):
    blob = " ".join(str(v) for v in fields.values())
    for sym, code in _CURRENCY_MAP.items():
        if sym in blob: return code
    for code in ("USD","EUR","GBP","JPY","INR","CNY","VND"):
        if code in blob.upper(): return code
    return "USD"

def parse_textract_expense(resp: dict) -> dict:
    lines, summary, currency = [], {}, "USD"
    for doc in resp.get("ExpenseDocuments", []):
        for f in doc.get("SummaryFields", []):
            t = (f.get("Type") or {}).get("Text", "")
            v = (f.get("ValueDetection") or {}).get("Text", "")
            summary[t] = v
        for group in doc.get("LineItemGroups", []):
            for li in group.get("LineItems", []):
                fields = {(f.get("Type") or {}).get("Text", ""):
                          (f.get("ValueDetection") or {}).get("Text", "")
                          for f in li.get("LineItemExpenseFields", [])}
                lines.append({"description": fields.get("ITEM", ""),
                    "quantity": int(_to_decimal(fields.get("QUANTITY", "1")) or 1),
                    "unit_price": _to_decimal(fields.get("UNIT_PRICE", "0")),
                    "total": _to_decimal(fields.get("PRICE", "0"))})
        currency = _detect_currency({**summary, **{l["description"]: str(l["unit_price"]) for l in lines}})
    return {"vendor_name": summary.get("VENDOR_NAME", ""),
            "total": _to_decimal(summary.get("TOTAL", "0")),
            "currency": currency, "lines": lines}
