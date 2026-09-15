import json
import os
import threading
import time
import urllib.parse
from datetime import datetime, timedelta

import httpx
import pandas as pd

from database.token_db import get_br_symbol, get_oa_symbol, get_token
from utils.httpx_client import get_httpx_client
from utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared, process-wide rate limiter for Angel market-data endpoints.
#
# Angel throttles per *client code* (account), so EVERY quote/history call in
# this process competes for the same per-second budget — the /tools option
# chain, the dashboard, and any background pollers all share it. A single
# module-level limiter paces them as one stream instead of each caller guessing
# with its own sleep.
#
# Real SmartAPI limits (see docs "Rate Limit"):
#   - /market/v1/quote        : 10 req/s
#   - /historical/.../*       :  3 req/s  (getCandleData; getOIData shares it)
# We pace slightly below those ceilings for headroom and rely on the 403/429
# retry-with-backoff in get_api_response() as the safety net. Both intervals are
# env-overridable so an account that is throttled harder can dial them up
# without a code change.
# ---------------------------------------------------------------------------
_rate_limit_lock = threading.Lock()
_last_call_ts = {"quote": 0.0, "history": 0.0}
QUOTE_MIN_INTERVAL = float(os.getenv("ANGEL_QUOTE_MIN_INTERVAL", "0.35"))      # ~2.8 req/s (safety under Angel 3-10 req/s limit)
HISTORY_MIN_INTERVAL = float(os.getenv("ANGEL_HISTORY_MIN_INTERVAL", "0.5"))   # ~2 req/s (limit 3)

# Short-lived quote cache to collapse duplicate bursts from UI / multiple strategies
QUOTE_CACHE_TTL = float(os.getenv("ANGEL_QUOTE_CACHE_TTL", "2.0"))             # 2.0s TTL
_quote_cache = {}                                                               # { "EXCHANGE:SYMBOL": (ts, data) }
_quote_cache_lock = threading.Lock()
_QUOTE_CACHE_FILE = "/tmp/openalgo_angel_quote_cache.json"


def _read_quote_cache(cache_key: str, ttl: float) -> dict | None:
    """Read cached quote from shared tmp file across Gunicorn and worker processes."""
    try:
        if os.path.exists(_QUOTE_CACHE_FILE):
            now = time.time()
            with open(_QUOTE_CACHE_FILE, "r") as f:
                content = json.load(f)
                item = content.get(cache_key)
                if item and (now - float(item.get("ts", 0))) < ttl:
                    return item.get("data")
    except Exception:
        pass
    return None


def _write_quote_cache(cache_key: str, quote_data: dict) -> None:
    """Write cached quote to shared tmp file so all processes share the cached quote."""
    try:
        now = time.time()
        content = {}
        if os.path.exists(_QUOTE_CACHE_FILE):
            try:
                with open(_QUOTE_CACHE_FILE, "r") as f:
                    content = json.load(f)
            except Exception:
                content = {}
        content[cache_key] = {"ts": now, "data": quote_data}
        # Keep only recent items under 60 seconds old
        content = {k: v for k, v in content.items() if (now - float(v.get("ts", 0))) < 60}
        with open(_QUOTE_CACHE_FILE, "w") as f:
            json.dump(content, f)
    except Exception:
        pass


def _apply_rate_limit(category: str) -> None:
    """Block just long enough to keep ``category`` under Angel's per-second cap.

    Cross-process safe using file-based lock timestamp in /tmp/ when running under Linux/Docker,
    with thread-lock fallback for other environments.
    """
    interval = HISTORY_MIN_INTERVAL if category == "history" else QUOTE_MIN_INTERVAL
    lock_file = f"/tmp/angel_rate_{category}.lock"
    sleep_for = 0.0

    with _rate_limit_lock:
        now = time.time()
        try:
            import fcntl
            with open(lock_file, "a+") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.seek(0)
                raw = f.read().strip()
                last_ts = float(raw) if raw else 0.0
                earliest = last_ts + interval
                if now < earliest:
                    sleep_for = earliest - now
                    target_ts = earliest
                else:
                    target_ts = now
                f.seek(0)
                f.truncate()
                f.write(f"{target_ts:.4f}")
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            earliest = _last_call_ts[category] + interval
            if now < earliest:
                sleep_for = earliest - now
                _last_call_ts[category] = earliest
            else:
                _last_call_ts[category] = now

    if sleep_for > 0:
        time.sleep(sleep_for)


def _penalize_rate_limit(category: str, penalty: float) -> None:
    """Back the WHOLE shared stream off after a broker rate-limit rejection.

    Pushes the shared next-allowed timestamp forward across processes so subsequent
    calls in all worker processes space out until pressure clears.
    """
    lock_file = f"/tmp/angel_rate_{category}.lock"
    with _rate_limit_lock:
        now = time.time()
        try:
            import fcntl
            with open(lock_file, "a+") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.seek(0)
                raw = f.read().strip()
                last_ts = float(raw) if raw else now
                base = max(last_ts, now)
                target_ts = base + penalty
                f.seek(0)
                f.truncate()
                f.write(f"{target_ts:.4f}")
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            base = max(_last_call_ts[category], now)
            _last_call_ts[category] = base + penalty


def get_api_response(endpoint, auth, method="GET", payload="", max_retries=3):
    """Helper function to make API calls to Angel One.

    Paces requests through the shared per-category rate limiter and transparently
    retries Angel's rate-limit rejection (HTTP 403 "exceeding access rate" / 429)
    with exponential backoff. A genuine auth 403 still fails fast.
    """
    AUTH_TOKEN = auth
    api_key = os.getenv("BROKER_API_KEY")

    # Get the shared httpx client with connection pooling
    client = get_httpx_client()

    headers = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": "CLIENT_LOCAL_IP",
        "X-ClientPublicIP": "CLIENT_PUBLIC_IP",
        "X-MACAddress": "MAC_ADDRESS",
        "X-PrivateKey": api_key,
    }

    if isinstance(payload, dict):
        payload = json.dumps(payload)

    url = f"https://apiconnect.angelone.in{endpoint}"
    category = "history" if "historical" in endpoint else "quote"

    for attempt in range(max_retries + 1):
        # Pace every attempt (including retries) through the shared limiter.
        _apply_rate_limit(category)

        if method == "GET":
            response = client.get(url, headers=headers)
        elif method == "POST":
            response = client.post(url, headers=headers, content=payload)
        else:
            response = client.request(method, url, headers=headers, content=payload)

        # Add status attribute for compatibility with the existing codebase
        response.status = response.status_code

        # Angel returns HTTP 403 for BOTH genuine auth failures AND rate-limit
        # breaches ("Access denied because of exceeding access rate"). 429 is
        # also used occasionally. Distinguish them: back off and retry the
        # rate-limit case, fail fast on a real auth error.
        if response.status_code in (403, 429):
            body_text = response.text
            lowered = body_text.lower()
            is_rate_limit = (
                response.status_code == 429
                or "exceeding" in lowered
                or "access rate" in lowered
                or "rate limit" in lowered
            )
            if is_rate_limit:
                if attempt < max_retries:
                    import random
                    jitter = random.uniform(0.1, 0.35)
                    backoff = 0.8 * (1.5**attempt) + jitter
                    # Throttle the whole shared stream across processes
                    _penalize_rate_limit(category, backoff)
                    logger.warning(
                        f"Angel rate limit hit on {endpoint} (status {response.status_code}); "
                        f"retry {attempt + 1}/{max_retries}, backing off {backoff:.2f}s"
                    )
                    time.sleep(backoff)
                    continue
                raise Exception("Angel API rate limit exceeded. Please retry shortly.")
            logger.debug(f"Debug - API returned 403 Forbidden. Header fields: {sorted(headers)}")
            logger.debug(f"Debug - Response text: {body_text}")
            raise Exception("Authentication failed. Please check your API key and auth token.")

        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            logger.error(f"Debug - Failed to parse response. Status code: {response.status_code}")
            logger.debug(f"Debug - Response text: {response.text}")
            raise Exception(f"Failed to parse API response (status {response.status_code})")

    # Exhausted retries without returning (all attempts were rate-limited).
    raise Exception("Angel API rate limit exceeded. Please retry shortly.")


class BrokerData:
    def __init__(self, auth_token):
        """Initialize Angel data handler with authentication token"""
        self.auth_token = auth_token
        # Map common timeframe format to Angel resolutions
        self.timeframe_map = {
            # Minutes
            "1m": "ONE_MINUTE",
            "3m": "THREE_MINUTE",
            "5m": "FIVE_MINUTE",
            "10m": "TEN_MINUTE",
            "15m": "FIFTEEN_MINUTE",
            "30m": "THIRTY_MINUTE",
            # Hours
            "1h": "ONE_HOUR",
            # Daily
            "D": "ONE_DAY",
        }

    def get_quotes(self, symbol: str, exchange: str) -> dict:
        """
        Get real-time quotes for given symbol
        Args:
            symbol: Trading symbol
            exchange: Exchange (e.g., NSE, BSE, NFO, BFO, CDS, MCX)
        Returns:
            dict: Quote data with required fields
        """
        try:
            cache_key = f"{exchange.upper()}:{symbol.upper()}"
            now = time.time()
            with _quote_cache_lock:
                cached = _quote_cache.get(cache_key)
                if cached and (now - cached[0]) < QUOTE_CACHE_TTL:
                    return cached[1].copy()

            disk_cached = _read_quote_cache(cache_key, QUOTE_CACHE_TTL)
            if disk_cached:
                with _quote_cache_lock:
                    _quote_cache[cache_key] = (now, disk_cached)
                return disk_cached.copy()

            # Convert symbol to broker format and get token
            br_symbol = get_br_symbol(symbol, exchange)
            token = get_token(symbol, exchange)

            if exchange == "NSE_INDEX":
                exchange = "NSE"
            elif exchange == "BSE_INDEX":
                exchange = "BSE"
            elif exchange == "MCX_INDEX":
                exchange = "MCX"

            # Prepare payload for Angel's quote API
            payload = {"mode": "FULL", "exchangeTokens": {exchange: [token]}}

            response = get_api_response(
                "/rest/secure/angelbroking/market/v1/quote/", self.auth_token, "POST", payload
            )

            if not response.get("status"):
                raise Exception(f"Error from Angel API: {response.get('message', 'Unknown error')}")

            # Extract quote data from response
            fetched_data = response.get("data", {}).get("fetched", [])
            if not fetched_data:
                raise Exception("No quote data received")

            quote = fetched_data[0]

            # Return quote in common format
            depth = quote.get("depth", {})
            bids = depth.get("buy", [])
            asks = depth.get("sell", [])

            res = {
                "bid": float(bids[0].get("price", 0)) if bids else 0,
                "ask": float(asks[0].get("price", 0)) if asks else 0,
                "open": float(quote.get("open", 0)),
                "high": float(quote.get("high", 0)),
                "low": float(quote.get("low", 0)),
                "ltp": float(quote.get("ltp", 0)),
                "prev_close": float(quote.get("close", 0)),
                "volume": int(quote.get("tradeVolume", 0)),
                "oi": int(quote.get("opnInterest", 0)),
            }
            with _quote_cache_lock:
                _quote_cache[cache_key] = (now, res)
            _write_quote_cache(cache_key, res)
            return res

        except Exception as e:
            raise Exception(f"Error fetching quotes: {str(e)}")

    def get_multiquotes(self, symbols: list) -> list:
        """
        Get real-time quotes for multiple symbols with automatic batching
        Args:
            symbols: List of dicts with 'symbol' and 'exchange' keys
                     Example: [{'symbol': 'SBIN', 'exchange': 'NSE'}, ...]
        Returns:
            list: List of quote data for each symbol with format:
                  [{'symbol': 'SBIN', 'exchange': 'NSE', 'data': {...}}, ...]
        """
        try:
            if not symbols:
                return []

            # Check quote cache first (both memory and disk)
            now = time.time()
            cached_results = []
            uncached_symbols = []
            with _quote_cache_lock:
                for item in symbols:
                    sym = item.get("symbol", "")
                    exch = item.get("exchange", "")
                    ck = f"{exch.upper()}:{sym.upper()}"
                    cached = _quote_cache.get(ck)
                    if cached and (now - cached[0]) < QUOTE_CACHE_TTL:
                        cached_results.append({
                            "symbol": sym,
                            "exchange": exch,
                            "data": cached[1].copy(),
                        })
                        continue
                    disk_cached = _read_quote_cache(ck, QUOTE_CACHE_TTL)
                    if disk_cached:
                        _quote_cache[ck] = (now, disk_cached)
                        cached_results.append({
                            "symbol": sym,
                            "exchange": exch,
                            "data": disk_cached.copy(),
                        })
                        continue
                    uncached_symbols.append(item)

            if not uncached_symbols:
                return cached_results

            symbols = uncached_symbols
            BATCH_SIZE = 50  # Angel API limit: 50 symbols per request

            # If symbols exceed batch size, process in batches
            if len(symbols) > BATCH_SIZE:
                logger.info(f"Processing {len(symbols)} symbols in batches of {BATCH_SIZE}")
                all_results = []

                # Split symbols into batches
                for i in range(0, len(symbols), BATCH_SIZE):
                    batch = symbols[i : i + BATCH_SIZE]
                    logger.debug(
                        f"Processing batch {i // BATCH_SIZE + 1}: symbols {i + 1} to {min(i + BATCH_SIZE, len(symbols))}"
                    )

                    # Process this batch (rate-limited inside get_api_response)
                    batch_results = self._process_quotes_batch(batch)
                    all_results.extend(batch_results)

                logger.info(
                    f"Successfully processed {len(all_results)} quotes in {(len(symbols) + BATCH_SIZE - 1) // BATCH_SIZE} batches"
                )
                return cached_results + all_results
            else:
                # Single batch processing
                return cached_results + self._process_quotes_batch(symbols)

        except Exception as e:
            logger.exception("Error fetching multiquotes")
            raise Exception(f"Error fetching multiquotes: {e}")

    def _process_quotes_batch(self, symbols: list) -> list:
        """
        Process a single batch of symbols (internal method)
        Args:
            symbols: List of dicts with 'symbol' and 'exchange' keys (max 50)
        Returns:
            list: List of quote data for the batch
        """
        # Group symbols by exchange and build token map
        exchange_tokens = {}  # {exchange: [token1, token2, ...]}
        token_map = {}  # {exchange:token -> {symbol, exchange, br_symbol}}
        skipped_symbols = []  # Track symbols that couldn't be resolved

        for item in symbols:
            symbol = item["symbol"]
            exchange = item["exchange"]

            try:
                br_symbol = get_br_symbol(symbol, exchange)
                token = get_token(symbol, exchange)

                # Track symbols that couldn't be resolved
                if not token:
                    logger.warning(
                        f"Skipping symbol {symbol} on {exchange}: could not resolve token"
                    )
                    skipped_symbols.append(
                        {"symbol": symbol, "exchange": exchange, "error": "Could not resolve token"}
                    )
                    continue

                # Normalize exchange for indices
                api_exchange = exchange
                if exchange == "NSE_INDEX":
                    api_exchange = "NSE"
                elif exchange == "BSE_INDEX":
                    api_exchange = "BSE"
                elif exchange == "MCX_INDEX":
                    api_exchange = "MCX"

                # Add token to exchange group
                if api_exchange not in exchange_tokens:
                    exchange_tokens[api_exchange] = []
                exchange_tokens[api_exchange].append(token)

                # Store mapping for response parsing
                token_map[f"{api_exchange}:{token}"] = {
                    "symbol": symbol,
                    "exchange": exchange,
                    "br_symbol": br_symbol,
                    "token": token,
                }

            except Exception as e:
                logger.warning(f"Skipping symbol {symbol} on {exchange}: {str(e)}")
                skipped_symbols.append({"symbol": symbol, "exchange": exchange, "error": str(e)})
                continue

        # Return skipped symbols if no valid tokens
        if not exchange_tokens:
            logger.warning("No valid tokens to fetch quotes for")
            return skipped_symbols

        # Prepare payload for Angel's quote API
        payload = {"mode": "FULL", "exchangeTokens": exchange_tokens}

        logger.info(
            f"Requesting quotes for {sum(len(t) for t in exchange_tokens.values())} instruments across {len(exchange_tokens)} exchanges"
        )
        logger.debug(f"Exchange tokens: {exchange_tokens}")

        # Make API call
        response = get_api_response(
            "/rest/secure/angelbroking/market/v1/quote/", self.auth_token, "POST", payload
        )

        if not response.get("status"):
            error_msg = f"Error from Angel API: {response.get('message', 'Unknown error')}"
            logger.error(error_msg)
            raise Exception(error_msg)

        # Parse response and build results
        results = []
        fetched_data = response.get("data", {}).get("fetched", [])
        unfetched_data = response.get("data", {}).get("unfetched", [])

        if unfetched_data:
            logger.warning(f"Some symbols could not be fetched: {unfetched_data}")

        # Create a lookup by exchange:token for quick access
        quotes_by_token = {}
        for quote in fetched_data:
            exchange = quote.get("exchange")
            token = quote.get("symbolToken")
            if exchange and token:
                quotes_by_token[f"{exchange}:{token}"] = quote

        # Build results from token_map
        for key, original in token_map.items():
            quote = quotes_by_token.get(key)

            if not quote:
                logger.warning(f"No quote data found for {original['symbol']} ({key})")
                results.append(
                    {
                        "symbol": original["symbol"],
                        "exchange": original["exchange"],
                        "error": "No quote data available",
                    }
                )
                continue

            # Parse and format quote data
            depth = quote.get("depth", {})
            bids = depth.get("buy", [])
            asks = depth.get("sell", [])

            result_item = {
                "symbol": original["symbol"],
                "exchange": original["exchange"],
                "data": {
                    "bid": float(bids[0].get("price", 0)) if bids else 0,
                    "ask": float(asks[0].get("price", 0)) if asks else 0,
                    "open": float(quote.get("open", 0)),
                    "high": float(quote.get("high", 0)),
                    "low": float(quote.get("low", 0)),
                    "ltp": float(quote.get("ltp", 0)),
                    "prev_close": float(quote.get("close", 0)),
                    "volume": int(quote.get("tradeVolume", 0)),
                    "oi": int(quote.get("opnInterest", 0)),
                },
            }
            with _quote_cache_lock:
                _quote_cache[f"{original['exchange'].upper()}:{original['symbol'].upper()}"] = (time.time(), result_item["data"])
            results.append(result_item)

        # Include skipped symbols in results
        return skipped_symbols + results

    def get_history(
        self, symbol: str, exchange: str, interval: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """
        Get historical data for given symbol
        Args:
            symbol: Trading symbol
            exchange: Exchange (e.g., NSE, BSE, NFO, BFO, CDS, MCX)
            interval: Candle interval (1m, 3m, 5m, 10m, 15m, 30m, 1h, D)
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            include_oi: Include open interest data (only for F&O contracts)
        Returns:
            pd.DataFrame: Historical data with columns [timestamp, open, high, low, close, volume, oi (if requested)]
        """
        try:
            # Convert symbol to broker format and get token
            br_symbol = get_br_symbol(symbol, exchange)

            token = get_token(symbol, exchange)
            logger.debug(f"Debug - Broker Symbol: {br_symbol}, Token: {token}")

            if exchange == "NSE_INDEX":
                exchange = "NSE"
            elif exchange == "BSE_INDEX":
                exchange = "BSE"
            elif exchange == "MCX_INDEX":
                exchange = "MCX"

            # Check for unsupported timeframes
            if interval not in self.timeframe_map:
                supported = list(self.timeframe_map.keys())
                raise Exception(
                    f"Timeframe '{interval}' is not supported by Angel. Supported timeframes are: {', '.join(supported)}"
                )

            # Convert dates to datetime objects
            from_date = pd.to_datetime(start_date)
            to_date = pd.to_datetime(end_date)

            # Set start time to 00:00 for the start date
            from_date = from_date.replace(hour=0, minute=0)

            # If end_date is today, set the end time to current time
            current_time = pd.Timestamp.now()
            if to_date.date() == current_time.date():
                to_date = current_time.replace(
                    second=0, microsecond=0
                )  # Remove seconds and microseconds
            else:
                # For past dates, set end time to 23:59
                to_date = to_date.replace(hour=23, minute=59)

            # Initialize empty list to store DataFrames
            dfs = []

            # Set chunk size based on interval as per Angel API documentation
            interval_limits = {
                "1m": 30,  # ONE_MINUTE
                "3m": 60,  # THREE_MINUTE
                "5m": 100,  # FIVE_MINUTE
                "10m": 100,  # TEN_MINUTE
                "15m": 200,  # FIFTEEN_MINUTE
                "30m": 200,  # THIRTY_MINUTE
                "1h": 400,  # ONE_HOUR
                "D": 2000,  # ONE_DAY
            }

            chunk_days = interval_limits.get(interval)
            if not chunk_days:
                supported = list(interval_limits.keys())
                raise Exception(
                    f"Interval '{interval}' not supported. Supported intervals: {', '.join(supported)}"
                )

            # Process data in chunks
            current_start = from_date
            while current_start <= to_date:
                # Calculate chunk end date
                current_end = min(current_start + timedelta(days=chunk_days - 1), to_date)

                # Prepare payload for historical data API
                payload = {
                    "exchange": exchange,
                    "symboltoken": token,
                    "interval": self.timeframe_map[interval],
                    "fromdate": current_start.strftime("%Y-%m-%d %H:%M"),
                    "todate": current_end.strftime("%Y-%m-%d %H:%M"),
                }
                logger.debug(f"Debug - Fetching chunk from {current_start} to {current_end}")
                logger.debug(f"Debug - API Payload: {payload}")

                # Fetch this chunk with chunk-level retries. A transient
                # rate-limit must NEVER cause us to skip a 30-day window —
                # doing so would punch a gap into a long 1-minute download.
                # get_api_response already retries its own rate-limit internally;
                # this is the outer safety net that keeps re-trying the SAME
                # chunk instead of advancing past it. `chunk_data` stays None
                # only if every attempt failed (→ loud gap warning); an empty
                # list means the chunk legitimately has no candles (weekend /
                # holiday / pre-listing) and is fine to skip.
                chunk_data = None
                for chunk_attempt in range(4):
                    try:
                        response = get_api_response(
                            "/rest/secure/angelbroking/historical/v1/getCandleData",
                            self.auth_token,
                            "POST",
                            payload,
                        )
                    except Exception as chunk_error:
                        msg = str(chunk_error).lower()
                        if "rate limit" in msg and chunk_attempt < 3:
                            backoff = 1.0 * (2**chunk_attempt)
                            logger.warning(
                                f"Rate limit on candle chunk {current_start} to {current_end}; "
                                f"retrying chunk ({chunk_attempt + 1}/4) in {backoff:.1f}s"
                            )
                            time.sleep(backoff)
                            continue
                        logger.error(
                            f"Debug - Error fetching chunk {current_start} to {current_end}: "
                            f"{chunk_error}"
                        )
                        break

                    if isinstance(response, dict) and response.get("status"):
                        chunk_data = response.get("data", []) or []
                        logger.debug(
                            f"Debug - Received {len(chunk_data)} candles for chunk "
                            f"{current_start} to {current_end}"
                        )
                        break

                    # status False / empty response — retry the chunk if Angel
                    # signalled a rate limit, otherwise give up on this chunk
                    # (e.g. bad token or genuinely no data for the range).
                    err_msg = response.get("message", "") if isinstance(response, dict) else ""
                    if ("exceed" in err_msg.lower() or "rate" in err_msg.lower()) and chunk_attempt < 3:
                        backoff = 1.0 * (2**chunk_attempt)
                        _penalize_rate_limit("history", backoff)
                        logger.warning(
                            f"Rate-limit response on candle chunk {current_start} to {current_end}; "
                            f"retrying chunk ({chunk_attempt + 1}/4) in {backoff:.1f}s"
                        )
                        time.sleep(backoff)
                        continue
                    logger.warning(
                        f"Debug - Error response for chunk {current_start} to {current_end}: "
                        f"{err_msg or 'unknown'}"
                    )
                    break

                if chunk_data:
                    chunk_df = pd.DataFrame(
                        chunk_data, columns=["timestamp", "open", "high", "low", "close", "volume"]
                    )
                    dfs.append(chunk_df)
                elif chunk_data is None:
                    # Every attempt failed — surface a loud, actionable warning
                    # instead of silently leaving a hole in the series.
                    logger.warning(
                        f"POSSIBLE GAP: could not fetch candle chunk {current_start} to "
                        f"{current_end} after 4 attempts"
                    )

                # Move to next chunk (inter-chunk pacing handled by the shared
                # history rate limiter inside get_api_response).
                current_start = current_end + timedelta(days=1)

            # If no data was found, return empty DataFrame
            if not dfs:
                logger.debug("Debug - No data received from API")
                return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

            # Combine all chunks
            df = pd.concat(dfs, ignore_index=True)

            # Convert timestamp to datetime
            df["timestamp"] = pd.to_datetime(df["timestamp"])

            # For daily timeframe, convert UTC to IST by adding 5 hours and 30 minutes
            if interval == "D":
                df["timestamp"] = df["timestamp"] + pd.Timedelta(hours=5, minutes=30)

            # Convert timestamp to Unix epoch
            df["timestamp"] = df["timestamp"].astype("int64") // 10**9  # Convert to Unix epoch

            # Ensure numeric columns and proper order
            numeric_columns = ["open", "high", "low", "close", "volume"]
            df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric)

            # Sort by timestamp and remove duplicates
            df = (
                df.sort_values("timestamp")
                .drop_duplicates(subset=["timestamp"])
                .reset_index(drop=True)
            )

            # Always fetch OI data for F&O contracts
            if exchange in ["NFO", "BFO", "CDS", "MCX"]:
                try:
                    oi_df = self.get_oi_history(symbol, exchange, interval, start_date, end_date)
                    if not oi_df.empty:
                        # Merge OI data with candle data
                        df = pd.merge(df, oi_df, on="timestamp", how="left")
                        # Fill any missing OI values with 0
                        df["oi"] = df["oi"].fillna(0).astype(int)
                    else:
                        # Add empty OI column if no data available
                        df["oi"] = 0
                except Exception as oi_error:
                    logger.error(f"Debug - Error fetching OI data: {str(oi_error)}")
                    # Add empty OI column on error
                    df["oi"] = 0

            # Reorder columns to match REST API format
            if "oi" in df.columns:
                df = df[["close", "high", "low", "open", "timestamp", "volume", "oi"]]
            else:
                # Add OI column with zeros if not present
                df["oi"] = 0
                df = df[["close", "high", "low", "open", "timestamp", "volume", "oi"]]

            return df

        except Exception as e:
            logger.error(f"Debug - Error: {str(e)}")
            raise Exception(f"Error fetching historical data: {str(e)}")

    def get_oi_history(
        self, symbol: str, exchange: str, interval: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """
        Get historical OI data for given symbol
        Args:
            symbol: Trading symbol
            exchange: Exchange (e.g., NFO, BFO, CDS, MCX)
            interval: Candle interval (1m, 3m, 5m, 10m, 15m, 30m, 1h, D)
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
        Returns:
            pd.DataFrame: Historical OI data with columns [timestamp, oi]
        """
        try:
            # Get token for the symbol
            token = get_token(symbol, exchange)

            # Convert dates to datetime objects
            from_date = pd.to_datetime(start_date)
            to_date = pd.to_datetime(end_date)

            # Set start time to 00:00 for the start date
            from_date = from_date.replace(hour=0, minute=0)

            # If end_date is today, set the end time to current time
            current_time = pd.Timestamp.now()
            if to_date.date() == current_time.date():
                to_date = current_time.replace(second=0, microsecond=0)
            else:
                # For past dates, set end time to 23:59
                to_date = to_date.replace(hour=23, minute=59)

            # Initialize empty list to store DataFrames
            dfs = []

            # Set chunk size based on interval (same as candle data)
            interval_limits = {
                "1m": 30,  # ONE_MINUTE
                "3m": 60,  # THREE_MINUTE
                "5m": 100,  # FIVE_MINUTE
                "10m": 100,  # TEN_MINUTE
                "15m": 200,  # FIFTEEN_MINUTE
                "30m": 200,  # THIRTY_MINUTE
                "1h": 400,  # ONE_HOUR
                "D": 2000,  # ONE_DAY
            }

            chunk_days = interval_limits.get(interval)
            if not chunk_days:
                raise Exception(f"Interval '{interval}' not supported for OI data")

            # Process data in chunks
            current_start = from_date
            while current_start <= to_date:
                # Calculate chunk end date
                current_end = min(current_start + timedelta(days=chunk_days - 1), to_date)

                # Prepare payload for OI data API
                payload = {
                    "exchange": exchange,
                    "symboltoken": token,
                    "interval": self.timeframe_map[interval],
                    "fromdate": current_start.strftime("%Y-%m-%d %H:%M"),
                    "todate": current_end.strftime("%Y-%m-%d %H:%M"),
                }

                try:
                    response = get_api_response(
                        "/rest/secure/angelbroking/historical/v1/getOIData",
                        self.auth_token,
                        "POST",
                        payload,
                    )

                    if not response or not response.get("status"):
                        logger.debug(
                            f"Debug - No OI data for chunk {current_start} to {current_end}"
                        )
                        current_start = current_end + timedelta(days=1)
                        continue

                except Exception as chunk_error:
                    logger.error(f"Debug - Error fetching OI chunk: {str(chunk_error)}")
                    current_start = current_end + timedelta(days=1)
                    continue

                # Extract OI data and create DataFrame
                data = response.get("data", [])
                if data:
                    chunk_df = pd.DataFrame(data)
                    # Rename 'time' to 'timestamp' for consistency
                    chunk_df.rename(columns={"time": "timestamp"}, inplace=True)
                    dfs.append(chunk_df)

                # Move to next chunk (inter-chunk pacing handled by the shared
                # history rate limiter inside get_api_response).
                current_start = current_end + timedelta(days=1)

            # If no data was found, return empty DataFrame
            if not dfs:
                return pd.DataFrame(columns=["timestamp", "oi"])

            # Combine all chunks
            df = pd.concat(dfs, ignore_index=True)

            # Convert timestamp to datetime
            df["timestamp"] = pd.to_datetime(df["timestamp"])

            # For daily timeframe, convert UTC to IST by adding 5 hours and 30 minutes
            if interval == "D":
                df["timestamp"] = df["timestamp"] + pd.Timedelta(hours=5, minutes=30)

            # Convert timestamp to Unix epoch
            df["timestamp"] = df["timestamp"].astype("int64") // 10**9

            # Ensure oi column is numeric
            df["oi"] = pd.to_numeric(df["oi"])

            # Sort by timestamp and remove duplicates
            df = (
                df.sort_values("timestamp")
                .drop_duplicates(subset=["timestamp"])
                .reset_index(drop=True)
            )

            return df

        except Exception as e:
            logger.error(f"Debug - Error fetching OI data: {str(e)}")
            # Return empty DataFrame on error
            return pd.DataFrame(columns=["timestamp", "oi"])

    def get_depth(self, symbol: str, exchange: str) -> dict:
        """
        Get market depth for given symbol
        Args:
            symbol: Trading symbol
            exchange: Exchange (e.g., NSE, BSE, NFO, BFO, CDS, MCX)
        Returns:
            dict: Market depth data with bids, asks and other details
        """
        try:
            # Convert symbol to broker format and get token
            br_symbol = get_br_symbol(symbol, exchange)
            token = get_token(symbol, exchange)

            if exchange == "NSE_INDEX":
                exchange = "NSE"
            elif exchange == "BSE_INDEX":
                exchange = "BSE"
            elif exchange == "MCX_INDEX":
                exchange = "MCX"

            # Prepare payload for market depth API
            payload = {"mode": "FULL", "exchangeTokens": {exchange: [token]}}

            response = get_api_response(
                "/rest/secure/angelbroking/market/v1/quote/", self.auth_token, "POST", payload
            )

            if not response.get("status"):
                raise Exception(f"Error from Angel API: {response.get('message', 'Unknown error')}")

            # Extract depth data
            fetched_data = response.get("data", {}).get("fetched", [])
            if not fetched_data:
                raise Exception("No depth data received")

            quote = fetched_data[0]
            depth = quote.get("depth", {})

            # Format bids and asks with exactly 5 entries each
            bids = []
            asks = []

            # Process buy orders (top 5)
            buy_orders = depth.get("buy", [])
            for i in range(5):  # Ensure exactly 5 entries
                if i < len(buy_orders):
                    bid = buy_orders[i]
                    bids.append({"price": bid.get("price", 0), "quantity": bid.get("quantity", 0)})
                else:
                    bids.append({"price": 0, "quantity": 0})

            # Process sell orders (top 5)
            sell_orders = depth.get("sell", [])
            for i in range(5):  # Ensure exactly 5 entries
                if i < len(sell_orders):
                    ask = sell_orders[i]
                    asks.append({"price": ask.get("price", 0), "quantity": ask.get("quantity", 0)})
                else:
                    asks.append({"price": 0, "quantity": 0})

            # Return depth data in common format matching REST API response
            return {
                "bids": bids,
                "asks": asks,
                "high": quote.get("high", 0),
                "low": quote.get("low", 0),
                "ltp": quote.get("ltp", 0),
                "ltq": quote.get("lastTradeQty", 0),
                "open": quote.get("open", 0),
                "prev_close": quote.get("close", 0),
                "volume": quote.get("tradeVolume", 0),
                "oi": quote.get("opnInterest", 0),
                "totalbuyqty": quote.get("totBuyQuan", 0),
                "totalsellqty": quote.get("totSellQuan", 0),
            }

        except Exception as e:
            raise Exception(f"Error fetching market depth: {str(e)}")
