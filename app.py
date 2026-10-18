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
