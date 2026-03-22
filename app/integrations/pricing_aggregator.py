"""Pricing Aggregator — merges multi-source pricing into best-price selection."""
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("pricing_aggregator")


def aggregate_pricing(
    api_results: List[Dict[str, Any]],
    historical_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Aggregate pricing from multiple supplier API results.
    Returns best price, average, and source breakdown.
    """
    if not api_results and historical_price is None:
        return {"best_price": None, "source": "none", "confidence": "none"}

    valid = [r for r in api_results if r.get("price") and r["price"] > 0]

    if not valid:
        if historical_price:
            return {
                "best_price": historical_price,
                "average_price": historical_price,
                "source": "historical",
                "confidence": "low",
                "supplier": "Historical data",
                "options": [],
            }
        return {"best_price": None, "source": "none", "confidence": "none"}

    # Sort by price
    sorted_results = sorted(valid, key=lambda r: r["price"])
    best = sorted_results[0]

    avg = sum(r["price"] for r in valid) / len(valid)

    # Cross-validate with historical
    confidence = "high" if len(valid) >= 2 else "medium"
    if historical_price:
        deviation = abs(best["price"] - historical_price) / max(historical_price, 0.01)
        if deviation > 0.5:
            confidence = "low"  # Large deviation from history

    return {
        "best_price": best["price"],
        "best_supplier": best.get("supplier", ""),
        "best_mpn": best.get("mpn", ""),
        "best_stock": best.get("stock"),
        "best_lead_days": best.get("lead_days"),
        "average_price": round(avg, 4),
        "source": "api",
        "confidence": confidence,
        "options_count": len(valid),
        "options": [
            {
                "supplier": r.get("supplier", ""),
                "price": r["price"],
                "stock": r.get("stock"),
                "lead_days": r.get("lead_days"),
            }
            for r in sorted_results[:5]
        ],
    }
