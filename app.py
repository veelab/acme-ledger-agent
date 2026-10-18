import os
import re
import json
import logging
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("acme-ledger")

EMAIL = os.getenv("EXAM_EMAIL", "24f1002845@ds.study.iitm.ac.in")
QUIZ_SIGN = os.getenv(
    "EXAM_QUIZ_SIGN",
    "CLp78A4gYjM38X9y+4nqTdNE1cKDa/e+lJSqFXAw5wltGS8ETdldtOrtHyVWdlRmt15n0WZTHL+YVTv+L3AF2PTs4XX9ZdFssMhzJ9GywoOSxgFDrBXRbKX+CNPwzNJo/A5ND2ogknHPP4TMv1fD1kcGzcDbxM3B4m69By0j/VFkJhKP4fHmQj2lmY5ykbGHHq1GBEjE0Kc4an1+wCvK0PitinK6114bAvNUW5yFb5uqXN1vDU2py/2zsD0movGQjkxD1eRyct6IpmdnDKbJ/3evpQ1WRW/hC1k9JR2RitKXHIgFJ4ZH3mNajk2ErmJOl5JBZhYTk4rQwCOttNjT/w==",
)
QUESTION_ID = os.getenv("EXAM_QUESTION_ID", "q-ledger-agent-server")
API_URL = os.getenv("EXAM_API_URL", "https://exam.sanand.workers.dev/questionData")
TZ = ZoneInfo("Asia/Kolkata")
USD_PER_UNIT = {"USD": 1.0, "EUR": 1.12, "INR": 0.01101}

app = FastAPI(title="Acme Appliances Order Ledger Agent", version="1.1.0")
session = requests.Session()
orders: list[dict[str, Any]] = []
rates = dict(USD_PER_UNIT)
last_refresh: Optional[str] = None


def api_get(path: str, page: Optional[int] = None) -> requests.Response:
    params = {
        "email": EMAIL,
        "quizSign": QUIZ_SIGN,
        "questionId": QUESTION_ID,
        "path": path,
    }
    if page is not None:
        params["page"] = page
    response = session.get(API_URL, params=params, timeout=12)
    response.raise_for_status()
    return response


def load_data() -> None:
    global orders, rates, last_refresh
    # The export endpoint returns newline-delimited JSON, one order per line.
    response = api_get("/export")
    text = response.text.strip()
    loaded: list[dict[str, Any]] = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            loaded = parsed
        elif isinstance(parsed, dict):
            for key in ("orders", "data", "records", "items"):
                if isinstance(parsed.get(key), list):
                    loaded = parsed[key]
                    break
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict) and "id" in row:
                    loaded.append(row)
            except json.JSONDecodeError:
                continue

    # Fallback to pagination if export format changes.
    if not loaded:
        page = 1
        while page <= 200:
            payload = api_get("/orders", page).json()
            batch = payload.get("orders", [])
            loaded.extend(batch)
            pages = int(payload.get("pages", page))
            if page >= pages or not batch:
                break
            page += 1

    if not loaded:
        raise RuntimeError("The ledger API returned no order rows")

    # Keep only the most recently updated version of each order id.
    latest: dict[str, dict[str, Any]] = {}
    for row in loaded:
        oid = str(row.get("id", ""))
        if not oid:
            continue
        old = latest.get(oid)
        if old is None or parse_dt(row.get("updated_at")) >= parse_dt(old.get("updated_at")):
            latest[oid] = row
    orders = list(latest.values())

    try:
        r = api_get("/rates").json()
        rates.update(r.get("usd_per_unit", {}))
    except Exception as exc:
        log.warning("Could not refresh rates; using known fallback rates: %s", exc)
    last_refresh = datetime.now(TZ).isoformat()
    log.info("Loaded %d raw rows and %d current orders", len(loaded), len(orders))


def parse_dt(value: Any) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=TZ)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return datetime.min.replace(tzinfo=TZ)


def amount_usd(row: dict[str, Any]) -> float:
    currency = str(row.get("currency", "USD")).upper()
    amount = float(row.get("amount", 0) or 0)
    return amount * float(rates.get(currency, 1.0))


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=TZ)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=TZ)
    else:
        end = datetime(year, month + 1, 1, tzinfo=TZ)
    return start, end


MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
REGIONS = ["North", "South", "East", "West", "Central"]
STATUSES = ["paid", "refunded", "void", "pending", "cancelled"]
PRODUCTS = ["Air Fryer", "Blender", "Grinder", "Juicer", "Kettle", "Mixer", "Rice Cooker", "Toaster"]


def extract_period(q: str) -> tuple[Optional[datetime], Optional[datetime]]:
    ql = q.lower()
    # Month plus year: "March 2026", "in Mar 2026".
    for name, month in MONTHS.items():
        m = re.search(rf"\b{name}\s+(20\d{{2}})\b", ql)
        if m:
            return month_bounds(int(m.group(1)), month)
    # Explicit date range: from YYYY-MM-DD to YYYY-MM-DD (inclusive end).
    m = re.search(r"\bfrom\s+(\d{4}-\d{2}-\d{2})\s+(?:to|through|until)\s+(\d{4}-\d{2}-\d{2})", ql)
    if m:
        d1, d2 = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
        return datetime.combine(d1, time.min, TZ), datetime.combine(d2 + timedelta(days=1), time.min, TZ)
    # Year only.
    m = re.search(r"\b(20\d{2})\b", ql)
    if m:
        y = int(m.group(1))
        return datetime(y, 1, 1, tzinfo=TZ), datetime(y + 1, 1, 1, tzinfo=TZ)
    return None, None


def filter_rows(q: str) -> list[dict[str, Any]]:
    ql = q.lower()
    start, end = extract_period(q)
    region = next((r for r in REGIONS if re.search(rf"\b{r.lower()}\b", ql)), None)
    product = next((p for p in PRODUCTS if p.lower() in ql), None)
    customer_match = re.search(r"\b(c\d{4})\b", ql, re.I)
    customer = customer_match.group(1).upper() if customer_match else None

    rows = list(orders)
    if region:
        rows = [r for r in rows if str(r.get("region", "")).lower() == region.lower()]
    if product:
        rows = [r for r in rows if str(r.get("product", "")).lower() == product.lower()]
    if customer:
        rows = [r for r in rows if str(r.get("customer", "")).upper() == customer]
    if start and end:
        rows = [r for r in rows if start <= parse_dt(r.get("created_at")) < end]
    return rows


def money(value: float) -> float:
    return round(value + 1e-9, 2)


def answer_question(question: str) -> Any:
    q = question.strip()
    ql = q.lower()
    rows = filter_rows(q)
    statuses = [s for s in STATUSES if re.search(rf"\b{s}\b", ql)]

    # Explicit status is the strongest filter. For revenue questions, paid is the default.
    if statuses:
        rows = [r for r in rows if str(r.get("status", "")).lower() in statuses]
    elif any(k in ql for k in ("revenue", "sales", "earn", "earned", "earning", "earnings", "made", "make", "generated", "generate", "paid order", "paid orders", "order worth", "order value")):

        rows = [r for r in rows if str(r.get("status", "")).lower() == "paid"]

    vals = [amount_usd(r) for r in rows]
    total = sum(vals)
    count = len(rows)

    asks_average = any(k in ql for k in ("average", "avg", "on average", "mean"))
    # Explicit distinct-customer wording must win over the generic customer
    # ranking detector below (which otherwise returns a customer ID).
    asks_distinct_customers = bool(re.search(
        r"\b(?:how many|number of|count of)?\s*(?:distinct|unique|different) customers\b",
        ql,
    )) or bool(re.search(
        r"\bhow many customers\b.*\b(?:bought|buy|purchased|placed|paid)\b",
        ql,
    ))
    asks_count = (
        asks_distinct_customers
        or any(k in ql for k in (
            "how many orders", "number of orders", "count of orders", "order count",
            "how many customers", "number of customers", "how many products", "count",
        ))
    )
    asks_refunds = any(k in ql for k in ("refund", "refunded"))
    asks_revenue = any(k in ql for k in ("revenue", "sales", "earn", "earned", "earning", "earnings", "made", "make", "generated", "generate", "total value", "total amount", "worth", "order value", "amount"))
    asks_product = any(k in ql for k in ("which product", "top product", "best-selling", "bestselling", "most sold", "most popular", "product sold", "by product"))
    asks_customer = any(k in ql for k in ("which customer", "top customer", "highest spending", "spent the most", "customer spent", "by customer", "customers"))

    if asks_refunds and not statuses:
        rows = [r for r in rows if str(r.get("status", "")).lower() == "refunded"]
        vals = [amount_usd(r) for r in rows]
        total, count = sum(vals), len(vals)

    # Handle distinct-customer counts explicitly before any ranking logic.
    # The status/product/region/date filters have already been applied to rows,
    # so "paid orders only" counts only customers with current paid orders.
    if asks_distinct_customers:
        return len({str(r.get("customer", "")).strip() for r in rows if str(r.get("customer", "")).strip()})

    # Generic count questions also precede customer/product ranking questions.
    if asks_count:
        if "customers" in ql:
            return len({str(r.get("customer", "")).strip() for r in rows if str(r.get("customer", "")).strip()})
        if "products" in ql:
            return len({str(r.get("product", "")).strip() for r in rows if str(r.get("product", "")).strip()})
        return count

    if asks_product:
        agg: dict[str, float] = {}
        counts: dict[str, int] = {}
        for r in rows:
            key = str(r.get("product", "Unknown"))
            agg[key] = agg.get(key, 0.0) + amount_usd(r)
            counts[key] = counts.get(key, 0) + int(r.get("qty", 0) or 0)
        if not agg:
            return "No matching orders were found."
        if any(k in ql for k in ("most sold", "best-selling", "bestselling", "most popular", "quantity", "units")):
            return max(counts, key=counts.get)
        return max(agg, key=agg.get)

    if asks_customer:
        agg: dict[str, float] = {}
        for r in rows:
            key = str(r.get("customer", "Unknown"))
            agg[key] = agg.get(key, 0.0) + amount_usd(r)
        if not agg:
            return "No matching orders were found."
        return max(agg, key=agg.get)

    if asks_average:
        if not rows:
            return 0
        # Average order value in USD; cents precision.
        return money(total / count)

    if asks_revenue or asks_refunds or any(k in ql for k in ("how much", "total", "sum")):
        return money(total)

    # General entity lookup: order id, customer, product or status summaries.
    order_id = re.search(r"\b(ord-\d+)\b", ql, re.I)
    if order_id:
        found = [r for r in orders if str(r.get("id", "")).lower() == order_id.group(1).lower()]
        if found:
            r = max(found, key=lambda x: parse_dt(x.get("updated_at")))
            return {
                "id": r.get("id"), "customer": r.get("customer"), "region": r.get("region"),
                "product": r.get("product"), "qty": r.get("qty"), "amount_usd": money(amount_usd(r)),
                "status": r.get("status"), "created_at": r.get("created_at"), "updated_at": r.get("updated_at")
            }

    if not rows:
        return "No matching orders were found."
    return {
        "matching_orders": count,
        "paid_orders": sum(1 for r in rows if r.get("status") == "paid"),
        "refunded_orders": sum(1 for r in rows if r.get("status") == "refunded"),
        "total_amount_usd": money(total),
    }


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@app.on_event("startup")
def startup_event() -> None:
    try:
        load_data()
    except Exception as exc:
        log.exception("Initial ledger load failed: %s", exc)


@app.get("/")
def health() -> dict[str, Any]:
    return {
        "service": "acme-ledger-agent",
        "status": "ok" if orders else "degraded",
        "current_orders": len(orders),
        "last_refresh": last_refresh,
    }


@app.post("/")
def ask(payload: QuestionRequest) -> dict[str, Any]:
    if not orders:
        try:
            load_data()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Ledger data unavailable: {exc}")
    try:
        return {"answer": answer_question(payload.question)}
    except Exception as exc:
        log.exception("Question handling failed")
        raise HTTPException(status_code=500, detail="Could not answer question")
