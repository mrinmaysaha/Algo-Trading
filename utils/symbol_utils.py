# utils/symbol_utils.py
"""
Shared symbol classification helpers used across the sandbox and other modules.
"""

from utils.constants import CRYPTO_EXCHANGES, FNO_EXCHANGES
from database.token_db_enhanced import fno_search_symbols
from utils.constants import INSTRUMENT_PERPFUT


def get_underlying_quote_symbol(base_symbol: str, exchange: str) -> str:
    """Return the quote symbol for an underlying, appending the crypto quote currency if needed.

    For crypto exchanges: canonical perpetual (e.g. BTCUSD.P)
    For all other exchanges: base_symbol unchanged
    """
    if exchange.upper() in CRYPTO_EXCHANGES:
        _perp = fno_search_symbols(
            underlying=base_symbol.upper(),
            exchange=exchange,
            instrumenttype=INSTRUMENT_PERPFUT,
            limit=1,
        )
        if _perp:
            return _perp[0]["symbol"]
        return f"{base_symbol.upper()}USD.P"
    return base_symbol


def is_option(symbol: str, exchange: str) -> bool:
    """Check if symbol is an option based on exchange and canonical symbol suffix."""
    # All exchanges (including CRYPTO) use canonical CE/PE suffix convention.
    # CRYPTO canonical format: BTC28FEB2580000CE / BTC28FEB2580000PE (no dashes)
    if exchange in FNO_EXCHANGES:
        return symbol.endswith("CE") or symbol.endswith("PE")
    return False


def is_future(symbol: str, exchange: str) -> bool:
    """Check if symbol is a future (or perpetual) based on exchange and canonical symbol suffix."""
    # For CRYPTO: dated futures end with FUT; perpetuals (e.g. BTCUSDT) are also futures.
    # Both are non-options so: is_future ≡ not is_option for the CRYPTO exchange.
    if exchange in CRYPTO_EXCHANGES:
        return not (symbol.endswith("CE") or symbol.endswith("PE"))
    if exchange in FNO_EXCHANGES - CRYPTO_EXCHANGES:
        return symbol.endswith("FUT")
    return False


def get_contract_multiplier(symbol: str, exchange: str) -> float:
    """
    Get price multiplier for contract value, trade value, and P&L calculations.

    MCX Commodity Quotation Rules:
    - GOLD / GOLDM / GOLDTEN: Quoted per 10g while Qty is in grams -> multiplier = 0.1
    - COTTONCNDY: Quoted per candy (2 bales) while Qty is in bales -> multiplier = 0.5
    - SILVER / SILVERM / SILVERMIC: Quoted per 1kg, Qty in kg -> multiplier = 1.0 (Qty * Price is exact)
    - CRUDEOIL / CRUDEOILM: Quoted per 1 bbl, Qty in bbl -> multiplier = 1.0 (Qty * Price is exact)
    - NATURALGAS / NATGASMINI / NATGASM: Quoted per 1 mmBtu, Qty in mmBtu -> multiplier = 1.0 (Qty * Price is exact)
    - Default multiplier = 1.0.
    """
    if not symbol or not exchange:
        return 1.0

    ex_upper = str(exchange).upper()
    sym_upper = str(symbol).upper()

    # MCX quotation unit rules:
    if ex_upper == "MCX":
        if (sym_upper.startswith("GOLDM") or sym_upper.startswith("GOLD")) and not (
            sym_upper.startswith("GOLDGUINEA") or sym_upper.startswith("GOLDPETAL")
        ):
            return 0.1
        if sym_upper.startswith("COTTONCNDY") or sym_upper.startswith("COTTON"):
            return 0.5

    try:
        from database.token_db import get_symbol_info

        sym_info = get_symbol_info(symbol, exchange)
        if sym_info and getattr(sym_info, "contract_value", None):
            cv = float(sym_info.contract_value)
            if cv > 0 and cv != 1.0:
                return cv
    except Exception:
        pass

    return 1.0


