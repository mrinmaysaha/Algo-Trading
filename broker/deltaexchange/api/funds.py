# api/funds.py
# Delta Exchange wallet balance → OpenAlgo margin format
# Endpoints:
#   GET /v2/wallet/balances      → available cash, blocked margin
#   GET /v2/positions/margined   → realized + unrealized PnL per position
#
# Note: Delta Exchange India's wallet/balances does not expose session P&L fields.
# P&L is aggregated from the positions endpoint instead.

import os

from broker.deltaexchange.api.baseurl import BASE_URL, get_auth_headers
from broker.deltaexchange.api.rate_limiter import PRIVATE, consume
from utils.httpx_client import get_httpx_client
from utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MARGIN_RESPONSE = {
    "availablecash": "0.00",
    "collateral": "0.00",
    "m2mrealized": "0.00",
    "m2munrealized": "0.00",
    "utiliseddebits": "0.00",
}


def _f(value):
    """Safe float conversion from string or number."""
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0


def _get_positions_pnl(api_key, api_secret):
    """
    Fetch open positions and return (total_realized_pnl, total_unrealized_pnl).
    Delta Exchange India's wallet/balances does not include session P&L fields,
    so we aggregate directly from /v2/positions/margined.
    """
    path = "/v2/positions/margined"
    url = BASE_URL + path
    try:
        # consume() before signing: it can block on the quota window and Delta
        # rejects signatures older than 5 seconds ("SignatureExpired").
        consume(path, method="GET", bucket=PRIVATE)
        headers = get_auth_headers(
            method="GET",
            path=path,
            query_string="",
            payload="",
            api_key=api_key,
            api_secret=api_secret,
        )
        client = get_httpx_client()
        response = client.get(url, headers=headers, timeout=30.0)
        if response.status_code != 200:
            logger.warning(
                f"[DeltaExchange] positions/margined HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
            return 0.0, 0.0
        data = response.json()
        if not data.get("success"):
            logger.warning(
                f"[DeltaExchange] positions/margined API error: {data.get('error', {})}"
            )
            return 0.0, 0.0
        positions = data.get("result", [])
        realized = sum(
            _f(p.get("realized_pnl")) for p in positions if isinstance(p, dict)
        )
        unrealized = sum(
            _f(p.get("unrealized_pnl")) for p in positions if isinstance(p, dict)
        )
        return realized, unrealized
    except Exception as e:
        logger.warning(f"[DeltaExchange] Could not fetch positions P&L: {e}")
        return 0.0, 0.0


def get_margin_data(auth_token):
    """
    Fetch wallet balance from Delta Exchange and return it in OpenAlgo margin format.

    Endpoint: GET /v2/wallet/balances
    Authentication: HMAC-SHA256 signed headers (api-key + timestamp + signature)

    Delta Exchange wallet balance object fields used:
        available_balance_inr – free balance in INR, immediately tradeable (availablecash)
        balance_inr           – total ledger balance in INR (totalbalance)
        blocked_margin        – total margin locked by open positions + orders (USD)

    P&L is sourced from /v2/positions/margined (not wallet/balances):
        m2mrealized    ← sum of realized_pnl across all open positions
        m2munrealized  ← sum of unrealized_pnl across all open positions

    OpenAlgo field mapping:
        availablecash  ← sum of available_balance_inr (actual free margin after blocked margin)
        totalbalance   ← sum of balance_inr across all wallets (spot + FNO combined in INR)
        collateral     ← sum of cross_locked_collateral across all wallets
        utiliseddebits ← total_balance_inr - total_available_inr (blocked margin in INR)

    Args:
        auth_token (str): api_key stored in OpenAlgo auth DB after login.

    Returns:
        dict: OpenAlgo standard margin dict, or DEFAULT_MARGIN_RESPONSE on failure.
    """
    api_key = auth_token
    api_secret = os.getenv("BROKER_API_SECRET", "")

    if not api_key or not api_secret:
        logger.error("[DeltaExchange] BROKER_API_KEY / BROKER_API_SECRET not set")
        return DEFAULT_MARGIN_RESPONSE

    path = "/v2/wallet/balances"
    url = BASE_URL + path

    try:
        # consume() before signing: it can block on the quota window and Delta
        # rejects signatures older than 5 seconds ("SignatureExpired").
        consume(path, method="GET", bucket=PRIVATE)
        headers = get_auth_headers(
            method="GET",
            path=path,
            query_string="",
            payload="",
            api_key=api_key,
            api_secret=api_secret,
        )

        client = get_httpx_client()
        response = client.get(url, headers=headers, timeout=30.0)

        if response.status_code != 200:
            logger.error(
                f"[DeltaExchange] wallet/balances HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
            return DEFAULT_MARGIN_RESPONSE

        data = response.json()
        logger.debug("[DeltaExchange] wallet/balances response received")

        if not data.get("success", False):
            error = data.get("error", {})
            logger.error(f"[DeltaExchange] wallet/balances API error: {error}")
            return DEFAULT_MARGIN_RESPONSE

        balances = data.get("result", [])
        if not isinstance(balances, list):
            logger.error(
                f"[DeltaExchange] Unexpected wallet/balances result type: {type(balances)}"
            )
            return DEFAULT_MARGIN_RESPONSE

        total_balance_inr = 0.0
        total_available_inr = 0.0
        total_collateral = 0.0
        total_balance_usd = 0.0
        total_available_usd = 0.0
        total_blocked_usd = 0.0

        for asset in balances:
            if not isinstance(asset, dict):
                continue
            b_inr = _f(asset.get("balance_inr", 0))
            a_inr = _f(asset.get("available_balance_inr", 0))
            b_usd = _f(asset.get("balance", 0))
            a_usd = _f(asset.get("available_balance", 0))
            blk_usd = _f(asset.get("blocked_margin", 0))

            total_balance_inr += b_inr
            total_available_inr += a_inr
            total_collateral += _f(asset.get("cross_locked_collateral", 0))

            total_balance_usd += b_usd
            total_available_usd += a_usd
            total_blocked_usd += blk_usd

        # Fallback if available_balance_inr is not provided but available_balance (USD) is present
        if total_available_inr == 0.0 and total_available_usd > 0.0:
            fx_rate = (total_balance_inr / total_balance_usd) if total_balance_usd > 0 else 88.0
            total_available_inr = total_available_usd * fx_rate

        total_blocked_inr = max(0.0, total_balance_inr - total_available_inr)

        # P&L comes from positions, not wallet balances
        total_realized_pnl, total_unrealized_pnl = _get_positions_pnl(api_key, api_secret)

        result = {
            "availablecash": f"{total_available_inr:.2f}",
            "collateral": f"{total_collateral:.2f}",
            "m2mrealized": f"{total_realized_pnl:.2f}",
            "m2munrealized": f"{total_unrealized_pnl:.2f}",
            "utiliseddebits": f"{total_blocked_inr:.2f}",
            "totalbalance": f"{total_balance_inr:.2f}",
            "availablecash_usd": f"{total_available_usd:.2f}",
            "totalbalance_usd": f"{total_balance_usd:.2f}",
            "utiliseddebits_usd": f"{total_blocked_usd:.2f}",
        }

        logger.debug(
            f"[DeltaExchange] Wallet: available_inr={result['availablecash']} "
            f"blocked_inr={result['utiliseddebits']} total_inr={result['totalbalance']} "
            f"realized={result['m2mrealized']} unrealized={result['m2munrealized']}"
        )
        return result

    except Exception as e:
        logger.error(f"[DeltaExchange] Unexpected error in get_margin_data: {e}", exc_info=True)
        return DEFAULT_MARGIN_RESPONSE
