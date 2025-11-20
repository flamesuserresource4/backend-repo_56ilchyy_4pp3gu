"""
Database Schemas for Monthly Bill Organizer

Each Pydantic model maps to a MongoDB collection with the lowercase class name.
- BudgetMonth -> "budgetmonth"
- Transaction -> "transaction"
- Alert -> "alert"
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import date

class PlannedExpense(BaseModel):
    name: str = Field(..., description="Label for the bill or expense (e.g., Rent)")
    category: str = Field(..., description="Category (e.g., rent, food, transport, savings)")
    amount: float = Field(..., ge=0, description="Planned amount for the month")
    due_day: Optional[int] = Field(None, ge=1, le=31, description="Day of month bill is typically due")
    recurring: bool = Field(True, description="Whether this repeats monthly")

class BudgetMonth(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-\d{2}$", description="Month in YYYY-MM format")
    income: float = Field(..., ge=0, description="Planned income for the month (e.g., salary)")
    notes: Optional[str] = Field(None, description="Optional notes about this month")
    planned_expenses: List[PlannedExpense] = Field(default_factory=list, description="List of planned bills/expenses")

    @field_validator("month")
    @classmethod
    def valid_month(cls, v: str) -> str:
        y, m = map(int, v.split("-"))
        if m < 1 or m > 12:
            raise ValueError("month must be in YYYY-MM with a valid month 01..12")
        return v

class Transaction(BaseModel):
    amount: float = Field(..., gt=0, description="Actual spend amount")
    category: str = Field(..., description="Category this spend belongs to")
    label: Optional[str] = Field(None, description="Optional label or merchant")
    tx_date: date = Field(..., description="Transaction date")

class Alert(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    type: str = Field(..., description="alert type: overspend | due_soon | low_budget")
    message: str
    level: str = Field("info", description="info | warning | danger")
