import os
from datetime import date, datetime
from calendar import monthrange
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db
from schemas import BudgetMonth, Transaction, Alert


def oid_str(doc: dict) -> dict:
    if not doc:
        return doc
    d = dict(doc)
    if d.get("_id"):
        d["_id"] = str(d["_id"])  # convert ObjectId to string
    return d


def start_end_for_month(month: str):
    year, m = map(int, month.split("-"))
    start = datetime(year, m, 1)
    last_day = monthrange(year, m)[1]
    end = datetime(year, m, last_day, 23, 59, 59, 999000)
    return start, end


app = FastAPI(title="Monthly Bill Organizer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "Monthly Bill Organizer Backend Running"}


@app.get("/schema")
def get_schema():
    return {
        "budgetmonth": BudgetMonth.model_json_schema(),
        "transaction": Transaction.model_json_schema(),
        "alert": Alert.model_json_schema(),
    }


@app.post("/api/budget/{month}")
def upsert_budget(month: str, payload: BudgetMonth):
    if payload.month != month:
        raise HTTPException(status_code=400, detail="Path month and payload month must match")
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    db["budgetmonth"].update_one({"month": month}, {"$set": payload.model_dump()}, upsert=True)
    doc = db["budgetmonth"].find_one({"month": month})
    return {"ok": True, "budget": oid_str(doc)}


@app.get("/api/budget/{month}")
def get_budget(month: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    plan = db["budgetmonth"].find_one({"month": month})
    if not plan:
        raise HTTPException(status_code=404, detail="No plan for this month")

    income = float(plan.get("income", 0))
    planned_total = sum(float(p.get("amount", 0)) for p in plan.get("planned_expenses", []))

    start, end = start_end_for_month(month)
    actual_cursor = db["transaction"].find({
        "date": {"$gte": start.date().isoformat(), "$lte": end.date().isoformat()},
    })
    actual_total = sum(float(t.get("amount", 0)) for t in actual_cursor)

    remaining_actual = income - actual_total

    today = datetime.utcnow().date()
    current_year, current_month = map(int, month.split("-"))
    last_day = monthrange(current_year, current_month)[1]
    last_date = date(current_year, current_month, last_day)
    first_date = date(current_year, current_month, 1)

    if today < first_date:
        days_left = (last_date - first_date).days + 1
    elif today > last_date:
        days_left = 0
    else:
        days_left = (last_date - today).days + 1

    daily_limit = remaining_actual / days_left if days_left > 0 else 0
    weekly_limit = daily_limit * 7

    planned_by_cat: Dict[str, float] = {}
    for p in plan.get("planned_expenses", []):
        planned_by_cat[p.get("category")] = planned_by_cat.get(p.get("category"), 0.0) + float(p.get("amount", 0))

    tx_cursor = db["transaction"].find({
        "date": {"$gte": start.date().isoformat(), "$lte": end.date().isoformat()},
    })
    actual_by_cat: Dict[str, float] = {}
    for t in tx_cursor:
        cat = t.get("category")
        actual_by_cat[cat] = actual_by_cat.get(cat, 0.0) + float(t.get("amount", 0))

    return {
        "plan": oid_str(plan),
        "metrics": {
            "income": income,
            "planned_total": planned_total,
            "remaining_planned": max(income - planned_total, 0.0),
            "actual_spent": actual_total,
            "remaining_actual": remaining_actual,
            "days_left": days_left,
            "daily_limit": daily_limit,
            "weekly_limit": weekly_limit,
            "planned_by_category": planned_by_cat,
            "actual_by_category": actual_by_cat,
        },
    }


@app.post("/api/transactions")
def add_transaction(tx: Transaction):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    data = tx.model_dump()
    # Store as ISO string date field named `date` for querying
    data["date"] = tx.tx_date.isoformat()
    db["transaction"].insert_one(data)
    return {"ok": True}


@app.get("/api/transactions")
def list_transactions(month: Optional[str] = Query(None, description="YYYY-MM to filter by month")):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    query = {}
    if month:
        start, end = start_end_for_month(month)
        query["date"] = {"$gte": start.date().isoformat(), "$lte": end.date().isoformat()}
    docs = list(db["transaction"].find(query).sort("date", 1))
    return [oid_str(d) for d in docs]


@app.get("/api/summary/{month}")
def month_summary(month: str):
    try:
        budget = get_budget(month)
    except HTTPException as e:
        if e.status_code == 404:
            budget = {"plan": None, "metrics": {}}
        else:
            raise

    metrics = budget.get("metrics", {})
    return {
        "month": month,
        "income": metrics.get("income", 0),
        "planned_total": metrics.get("planned_total", 0),
        "actual_spent": metrics.get("actual_spent", 0),
        "remaining_actual": metrics.get("remaining_actual", 0),
        "daily_limit": metrics.get("daily_limit", 0),
        "weekly_limit": metrics.get("weekly_limit", 0),
        "planned_by_category": metrics.get("planned_by_category", {}),
        "actual_by_category": metrics.get("actual_by_category", {}),
    }


@app.get("/api/alerts/{month}")
def get_alerts(month: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    alerts: List[dict] = []

    try:
        data = get_budget(month)
    except HTTPException as e:
        if e.status_code == 404:
            return []
        raise

    plan = data["plan"]
    metrics = data["metrics"]

    planned_by_category = metrics.get("planned_by_category", {})
    actual_by_category = metrics.get("actual_by_category", {})
    for cat, planned_amt in planned_by_category.items():
        actual_amt = actual_by_category.get(cat, 0.0)
        if actual_amt > planned_amt and planned_amt > 0:
            alerts.append({
                "month": month,
                "type": "overspend",
                "message": f"Spending in {cat} is over plan (planned {planned_amt:.2f}, actual {actual_amt:.2f}).",
                "level": "warning" if actual_amt <= planned_amt * 1.25 else "danger",
            })

    remaining = metrics.get("remaining_actual", 0.0)
    income = metrics.get("income", 0.0)
    if income > 0:
        remaining_ratio = remaining / income
        if remaining_ratio <= 0.1:
            alerts.append({
                "month": month,
                "type": "low_budget",
                "message": "Remaining monthly budget is below 10% of income.",
                "level": "warning" if remaining > 0 else "danger",
            })

    if plan and plan.get("planned_expenses"):
        today = datetime.utcnow().date()
        year, m = map(int, month.split("-"))
        last_day = monthrange(year, m)[1]
        for p in plan["planned_expenses"]:
            due_day = p.get("due_day")
            if not due_day:
                continue
            due_day = min(max(int(due_day), 1), last_day)
            due_date = date(year, m, due_day)
            days_until = (due_date - today).days
            if 0 <= days_until <= 5:
                alerts.append({
                    "month": month,
                    "type": "due_soon",
                    "message": f"{p.get('name')} is due in {days_until} day(s).",
                    "level": "info" if days_until >= 3 else "warning",
                })

    return alerts


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name if hasattr(db, "name") else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                response["collections"] = db.list_collection_names()[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
