#!/usr/bin/env python
"""Wayfarer Currency MCP server.

Backed by **Frankfurter** (api.frankfurter.app) -- a free, keyless, open-source
FX service that republishes European Central Bank reference rates. Unlike the
flight and hotel prices in this project, these rates are *real*: the ECB
publishes them daily and Frankfurter passes them through unmodified.

Tools
-----
``convert``            Convert an amount between currencies at the latest rate.
``get_rates``          Fetch rates for one base against several currencies.
``convert_budget``     Turn a user's budget (stated in their own currency) into
                       the EUR the planner works in, and back again.
``historical_rate``    Rate on a past date, for "what did it cost last year".
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_servers.common.http import HttpError, get_json
from mcp_servers.common.server_compat import create_server

FRANKFURTER_URL = os.getenv("FRANKFURTER_URL", "https://api.frankfurter.app")

mcp = create_server("wayfarer-currency")

# ECB reference rates cover these currencies. Anything outside the set returns
# a clear error rather than a silently wrong number.
SUPPORTED = {
    "AUD", "BGN", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP",
    "HKD", "HUF", "IDR", "ILS", "INR", "ISK", "JPY", "KRW", "MXN", "MYR",
    "NOK", "NZD", "PHP", "PLN", "RON", "SEK", "SGD", "THB", "TRY", "USD", "ZAR",
}

# Common symbols so the Query Analyst can read "$500" or "£1200" correctly.
SYMBOL_TO_CODE = {
    "$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY",
    "₹": "INR", "₩": "KRW", "R$": "BRL", "C$": "CAD", "A$": "AUD",
    "CHF": "CHF", "kr": "SEK", "zł": "PLN", "₺": "TRY",
}


def _normalise(code: str) -> str:
    raw = (code or "").strip()
    if raw in SYMBOL_TO_CODE:
        return SYMBOL_TO_CODE[raw]
    return raw.upper()


@mcp.tool()
async def convert(amount: float, from_currency: str, to_currency: str) -> dict[str, Any]:
    """Convert an amount between two currencies at the latest ECB rate.

    Args:
        amount: Amount to convert.
        from_currency: ISO 4217 code or common symbol (e.g. "USD" or "$").
        to_currency: ISO 4217 code or common symbol.
    """
    source = _normalise(from_currency)
    target = _normalise(to_currency)

    if source not in SUPPORTED:
        return {"error": f"Unsupported source currency {source!r}.",
                "supported": sorted(SUPPORTED)}
    if target not in SUPPORTED:
        return {"error": f"Unsupported target currency {target!r}.",
                "supported": sorted(SUPPORTED)}
    if source == target:
        return {
            "amount": amount, "from": source, "to": target,
            "rate": 1.0, "converted": round(float(amount), 2),
            "as_of": date.today().isoformat(), "source": "identity",
        }

    try:
        payload = await get_json(
            f"{FRANKFURTER_URL}/latest",
            {"from": source, "to": target},
            cache_ttl=21600,  # ECB publishes once per working day.
        )
    except HttpError as exc:
        return {"error": f"FX lookup failed: {exc}"}

    rate = (payload.get("rates") or {}).get(target)
    if rate is None:
        return {"error": f"No rate returned for {source}->{target}."}

    return {
        "amount": float(amount),
        "from": source,
        "to": target,
        "rate": rate,
        "converted": round(float(amount) * float(rate), 2),
        "as_of": payload.get("date"),
        "source": "frankfurter / ECB reference rates",
        "data_basis": "real",
    }


@mcp.tool()
async def get_rates(base: str = "EUR", targets: list[str] | None = None) -> dict[str, Any]:
    """Latest rates for one base currency against several targets.

    Args:
        base: Base ISO 4217 code.
        targets: Target codes. Defaults to a common travel basket.
    """
    base_code = _normalise(base)
    if base_code not in SUPPORTED:
        return {"error": f"Unsupported base currency {base_code!r}.",
                "supported": sorted(SUPPORTED)}

    wanted = [_normalise(t) for t in (targets or ["USD", "GBP", "JPY", "CHF", "TRY", "THB"])]
    wanted = [t for t in wanted if t in SUPPORTED and t != base_code]

    params: dict[str, Any] = {"from": base_code}
    if wanted:
        params["to"] = ",".join(wanted)

    try:
        payload = await get_json(f"{FRANKFURTER_URL}/latest", params, cache_ttl=21600)
    except HttpError as exc:
        return {"error": f"FX lookup failed: {exc}"}

    return {
        "base": base_code,
        "rates": payload.get("rates") or {},
        "as_of": payload.get("date"),
        "source": "frankfurter / ECB reference rates",
        "data_basis": "real",
    }


@mcp.tool()
async def convert_budget(
    amount: float,
    user_currency: str,
    destination_currency: str | None = None,
) -> dict[str, Any]:
    """Express a traveller's budget in EUR (the planner's unit) and locally.

    The planner does all arithmetic in EUR because the cost models are
    EUR-denominated. This tool is the boundary translation: it takes a budget
    in whatever the user said, and returns the EUR figure the graph uses plus
    the destination-currency figure for the on-the-ground briefing.

    Args:
        amount: Budget amount as the user stated it.
        user_currency: The currency the user is thinking in.
        destination_currency: Local currency at the destination, if known.
    """
    result: dict[str, Any] = {"stated": {"amount": float(amount),
                                         "currency": _normalise(user_currency)}}

    eur = await convert(amount, user_currency, "EUR")
    if "error" in eur:
        return {**result, "error": eur["error"]}
    result["in_eur"] = {"amount": eur["converted"], "rate": eur["rate"],
                        "as_of": eur["as_of"]}

    if destination_currency:
        local = await convert(amount, user_currency, destination_currency)
        if "error" not in local:
            result["in_destination_currency"] = {
                "amount": local["converted"],
                "currency": local["to"],
                "rate": local["rate"],
            }
        else:
            result["destination_currency_note"] = local["error"]

    result["source"] = "frankfurter / ECB reference rates"
    result["data_basis"] = "real"
    return result


@mcp.tool()
async def historical_rate(
    on_date: str,
    from_currency: str = "EUR",
    to_currency: str = "USD",
) -> dict[str, Any]:
    """Exchange rate on a specific past date (ECB series starts 1999-01-04).

    Args:
        on_date: ISO date YYYY-MM-DD.
        from_currency: Base ISO code.
        to_currency: Target ISO code.
    """
    source, target = _normalise(from_currency), _normalise(to_currency)
    if source not in SUPPORTED or target not in SUPPORTED:
        return {"error": "Unsupported currency pair.", "supported": sorted(SUPPORTED)}

    try:
        parsed = datetime.strptime(on_date[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {"error": f"Invalid date {on_date!r}. Use YYYY-MM-DD."}
    if parsed < date(1999, 1, 4):
        return {"error": "ECB reference rates begin on 1999-01-04."}

    try:
        payload = await get_json(
            f"{FRANKFURTER_URL}/{parsed.isoformat()}",
            {"from": source, "to": target},
            cache_ttl=604800,  # A past rate never changes.
        )
    except HttpError as exc:
        return {"error": f"FX lookup failed: {exc}"}

    return {
        "requested_date": parsed.isoformat(),
        # Frankfurter returns the previous working day on weekends/holidays.
        "effective_date": payload.get("date"),
        "from": source,
        "to": target,
        "rate": (payload.get("rates") or {}).get(target),
        "source": "frankfurter / ECB reference rates",
        "data_basis": "real",
    }


def main() -> None:
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
