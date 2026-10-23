from __future__ import annotations

from typing import Any


VERSION = "product-execution-bridge@0.1.0"
DEFAULT_MAX_QUOTE_AGE_SECONDS = 15 * 60
EXECUTION_PHASES = {"OPEN"}


def _positive_weights(weights: dict[str, float] | None) -> dict[str, float]:
    return {
        str(symbol): max(0.0, float(weight))
        for symbol, weight in (weights or {}).items()
        if float(weight) > 1e-12
    }


def _capacity_rows(capital_capacity: dict | None) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = {}
    for sleeve in (capital_capacity or {}).get("sleeves") or []:
        sleeve_id = str(sleeve.get("sleeve_id") or "")
        for product in sleeve.get("products") or []:
            symbol = str(product.get("symbol") or "")
            if not symbol:
                continue
            rows.setdefault(symbol, []).append({
                "sleeve_id": sleeve_id,
                "starting_capital_usd": sleeve.get("starting_capital_usd"),
                "capacity_status": sleeve.get("capacity_status"),
                "target_notional_usd": product.get("target_notional_usd"),
                "adv20_notional_usd": product.get("adv20_notional_usd"),
                "planned_participation_adv": product.get("planned_participation_adv"),
                "minimum_execution_days": product.get("minimum_execution_days"),
                "all_in_bps_per_side": product.get("all_in_bps_per_side"),
                "estimated_entry_cost_usd": product.get("estimated_entry_cost_usd"),
            })
    return rows


def _quote_state(
    receipt: dict | None,
    *,
    max_quote_age_seconds: float,
) -> dict:
    if not receipt:
        return {
            "status": "MISSING",
            "verified": False,
            "price": None,
            "age_seconds": None,
            "source": None,
            "source_timestamp": None,
        }
    price = receipt.get("price")
    try:
        price_value = float(price)
    except (TypeError, ValueError):
        price_value = 0.0
    age = receipt.get("age_seconds")
    try:
        age_value = float(age)
    except (TypeError, ValueError):
        age_value = None
    if price_value <= 0.0:
        status = "INVALID_PRICE"
        verified = False
    elif age_value is None:
        status = "AGE_UNVERIFIED"
        verified = False
    elif age_value < 0.0:
        status = "INVALID_AGE"
        verified = False
    elif age_value > float(max_quote_age_seconds):
        status = "STALE"
        verified = False
    else:
        status = "VERIFIED_FRESH"
        verified = True
    return {
        "status": status,
        "verified": verified,
        "price": price_value if price_value > 0.0 else None,
        "age_seconds": age_value,
        "source": receipt.get("source"),
        "source_timestamp": receipt.get("source_timestamp"),
        "receipt_timestamp": receipt.get("receipt_timestamp"),
    }


def build_product_execution_plan(
    target_asset_weights: dict[str, float] | None,
    capital_capacity: dict | None,
    input_phase: str | None,
    quote_receipts: dict[str, dict] | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> dict:
    """Bridge frozen portfolio targets to verified buyable-product simulation inputs.

    The bridge never creates a broker order. A product can become simulation-ready
    only during an allowed execution phase, with a positive fresh quote no older
    than the fixed freshness threshold and with usable capacity evidence.
    """
    phase = str(input_phase or "").upper() or None
    targets = _positive_weights(target_asset_weights)
    capacity = _capacity_rows(capital_capacity)
    receipts = quote_receipts or {}

    if not targets:
        return {
            "version": VERSION,
            "status": "NO_RISK_POSITION",
            "input_phase": phase,
            "max_quote_age_seconds": float(max_quote_age_seconds),
            "all_required_quotes_verified": True,
            "all_capacity_verified": True,
            "products": [],
            "broker_execution_enabled": False,
            "real_order_authorized": False,
            "simulation_only": True,
        }

    products = []
    all_quotes_verified = True
    all_capacity_verified = True
    for symbol, weight in sorted(targets.items()):
        quote = _quote_state(
            receipts.get(symbol),
            max_quote_age_seconds=max_quote_age_seconds,
        )
        sleeve_rows = capacity.get(symbol) or []
        capacity_verified = bool(sleeve_rows) and all(
            str(row.get("capacity_status") or "") != "LIQUIDITY_DATA_UNAVAILABLE"
            and row.get("minimum_execution_days") is not None
            for row in sleeve_rows
        )
        all_quotes_verified = all_quotes_verified and bool(quote["verified"])
        all_capacity_verified = all_capacity_verified and capacity_verified
        products.append({
            "symbol": symbol,
            "target_weight": float(weight),
            "quote_verification": quote,
            "capacity_verified": capacity_verified,
            "capital_sleeves": sleeve_rows,
            "product_state": (
                "READY"
                if quote["verified"] and capacity_verified and phase in EXECUTION_PHASES
                else "BLOCKED"
            ),
        })

    if phase not in EXECUTION_PHASES:
        status = "REVIEW_ONLY_OUTSIDE_EXECUTION_SESSION"
    elif not all_quotes_verified:
        status = "BLOCKED_QUOTE_VERIFICATION"
    elif not all_capacity_verified:
        status = "BLOCKED_CAPACITY_VERIFICATION"
    else:
        status = "READY_FOR_SIMULATED_EXECUTION"

    return {
        "version": VERSION,
        "status": status,
        "input_phase": phase,
        "allowed_execution_phases": sorted(EXECUTION_PHASES),
        "max_quote_age_seconds": float(max_quote_age_seconds),
        "all_required_quotes_verified": bool(all_quotes_verified),
        "all_capacity_verified": bool(all_capacity_verified),
        "target_product_count": len(products),
        "target_risk_weight": sum(targets.values()),
        "products": products,
        "broker_execution_enabled": False,
        "real_order_authorized": False,
        "simulation_only": True,
        "semantics": (
            "INDEX LEVELS NEVER COUNT AS FILLS. TARGET WEIGHTS ARE NOT EXECUTIONS. "
            "ONLY FRESH VERIFIED BUYABLE-PRODUCT QUOTES PLUS CAPACITY EVIDENCE CAN "
            "AUTHORIZE A SIMULATED EXECUTION RECORD. BROKER EXECUTION REMAINS DISABLED."
        ),
    }
