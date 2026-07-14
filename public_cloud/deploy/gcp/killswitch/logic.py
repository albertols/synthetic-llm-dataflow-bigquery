"""Pure decision logic for the billing killswitch. No cloud imports."""
from __future__ import annotations


def should_kill(payload: dict, threshold: float) -> bool:
    """True when spend has reached `threshold` fraction of the budget."""
    budget = payload.get("budgetAmount")
    cost = payload.get("costAmount")
    if not budget or cost is None:
        return False
    return (cost / budget) >= threshold
