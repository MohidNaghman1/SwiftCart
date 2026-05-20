from decimal import Decimal
from datetime import datetime
from django.utils import timezone

def calculate_unused_value(membership):
    today = timezone.now()
    if not membership.end_date or not membership.start_date:
        return Decimal("0.00")
    total_days = (membership.end_date - membership.start_date).days
    if total_days <= 0:
        return Decimal("0.00")
    days_remaining = (membership.end_date - today).days
    if days_remaining <= 0:
        return Decimal("0.00")
    unused_value = (Decimal(days_remaining) / Decimal(total_days)) * membership.plan.price
    return round(unused_value, 2)

def calculate_switch_cost(membership, new_plan):
    unused_value = calculate_unused_value(membership)
    amount_due = new_plan.price - unused_value
    return {
        "unused_value": unused_value,
        "new_plan_price": new_plan.price,
        "amount_due": max(amount_due, Decimal("0.00")),
        "is_eligible": new_plan.price >= unused_value,
        "is_free": amount_due <= Decimal("0.00"),
    }

def get_billing_label(duration_months):
    if duration_months == 1:
        return "Monthly"
    if duration_months == 6:
        return "Every 6 months"
    if duration_months == 12:
        return "Yearly"
    return f"Every {duration_months} months"
