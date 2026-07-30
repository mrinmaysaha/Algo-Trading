# backtest_sr_breakout.py
"""
Backtest: SR Breakout + 0.1% Pullback Strategy
Uses OpenAlgo API data directly
"""

import os
import sys
import diskcache
from datetime import datetime, timezone

# ── Cache patch ───────────────────────────────────────────────────────────────
LOCAL_CACHE_DIR = os.path.join(os.path.expanduser("~"), "openalgo_cache", "ltp_bulk")
os.makedirs(LOCAL_CACHE_DIR, exist_ok=True)

_OriginalCache = diskcache.Cache
class PatchedCache(_OriginalCache):
    def __init__(self, directory=None, *args, **kwargs):
        if directory and 'sudranga1' in str(directory):
            directory = LOCAL_CACHE_DIR
        super().__init__(directory, *args, **kwargs)
diskcache.Cache = PatchedCache
# ─────────────────────────────────────────────────────────────────────────────

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import backtrader as bt
import backtrader.analyzers as btanalyzers
import pandas as pd
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY  = os.getenv("OPENALGO_API_KEY", "")
API_HOST = os.getenv("OPENALGO_API_HOST", "http://127.0.0.1:5000")

# ── Strategy Parameters (mirror your live strategy) ───────────────────────────
SYMBOLS = {
    "RELIANCE": {
        "exchange"  : "NSE",
        "support"   : 1350.0,   # ← Update to realistic levels
        "resistance": 1450.0,   # ← Update to realistic levels
        "qty"       : 1,
    },
    "TCS": {
        "exchange"  : "NSE",
        "support"   : 3300.0,
        "resistance": 3500.0,
        "qty"       : 1,
    },
}

TARGET_PCT   = 0.02
SL_PCT       = 0.01
TRAIL_PCT    = 0.002
PULLBACK_PCT = 0.001

FROM_DATE    = "2025-06-01"
TO_DATE      = "2025-07-25"
INTERVAL     = "5m"
INITIAL_CASH = 500_000.0
COMMISSION   = 0.0003   # 0.03% per side
# ─────────────────────────────────────────────────────────────────────────────


# ── Data Fetch ────────────────────────────────────────────────────────────────
def detect_timestamp(series):
    sample = float(series.iloc[0])
    if   sample > 1e18: unit = "ns"
    elif sample > 1e12: unit = "ms"
    else              : unit = "s"

    dt = pd.to_datetime(series, unit=unit, utc=True)
    try:
        from zoneinfo import ZoneInfo
        dt = dt.dt.tz_convert(ZoneInfo("Asia/Kolkata"))
    except Exception:
        import pytz
        dt = dt.dt.tz_convert(pytz.timezone("Asia/Kolkata"))

    return dt.dt.tz_localize(None)


def fetch_data(symbol, exchange, interval, start, end):
    url = f"{API_HOST}/api/v1/history"
    payload = {
        "apikey"    : API_KEY,
        "symbol"    : symbol,
        "exchange"  : exchange,
        "interval"  : interval,
        "start_date": start,
        "end_date"  : end,
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    resp = r.json()

    records = None
    for key in ["data", "candles", "ohlcv"]:
        if key in resp and isinstance(resp[key], list) and resp[key]:
            records = resp[key]
            break

    if not records:
        print(f"❌ No data for {symbol}")
        return None

    df = pd.DataFrame(records)

    # Find timestamp column
    ts_col = next(
        (c for c in df.columns if c.lower() in
         ["timestamp", "time", "date", "datetime"]),
        df.columns[0]
    )

    # Detect if numeric (unix) or string
    if pd.api.types.is_numeric_dtype(df[ts_col]):
        df["datetime"] = detect_timestamp(df[ts_col])
    else:
        df["datetime"] = pd.to_datetime(df[ts_col], utc=True).dt.tz_localize(None)

    df = df.drop(columns=[ts_col])
    df = df.set_index("datetime").sort_index()

    # Rename columns
    rename = {}
    for col in df.columns:
        cl = col.lower()
        if   cl == "open"             : rename[col] = "open"
        elif cl == "high"             : rename[col] = "high"
        elif cl == "low"              : rename[col] = "low"
        elif cl in ("close","ltp")    : rename[col] = "close"
        elif cl in ("volume","vol")   : rename[col] = "volume"
    df = df.rename(columns=rename)

    keep = [c for c in ["open","high","low","close","volume"] if c in df.columns]
    df   = df[keep].copy()
    if "volume" not in df.columns:
        df["volume"] = 0.0

    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna()

    if df.index[0].year < 2000:
        print(f"❌ Bad timestamp for {symbol}: {df.index[0]}")
        return None

    print(f"✅ {symbol}: {len(df)} bars  |  "
          f"{df.index[0].strftime('%Y-%m-%d %H:%M')} → "
          f"{df.index[-1].strftime('%Y-%m-%d %H:%M')}")
    return df


class PandasFeed(bt.feeds.PandasData):
    params = (
        ("datetime",     None),
        ("open",         "open"),
        ("high",         "high"),
        ("low",          "low"),
        ("close",        "close"),
        ("volume",       "volume"),
        ("openinterest", -1),
    )


# ── Strategy ──────────────────────────────────────────────────────────────────
class SRBreakoutPullback(bt.Strategy):
    """
    Mirrors your live strategy logic exactly:
    - Candle1 closes above resistance → Candle2 confirms higher close → BUY
    - Candle1 closes below support    → Candle2 confirms lower close  → SELL
    - Entry: LTP touches 0.1% pullback level
    - Target: 2% | SL: 1% | Trail: 0.2%
    - One trade per symbol per day
    """

    params = (
        ("support"   , 1350.0),
        ("resistance", 1450.0),
        ("target_pct"   , TARGET_PCT),
        ("sl_pct"       , SL_PCT),
        ("trail_pct"    , TRAIL_PCT),
        ("pullback_pct" , PULLBACK_PCT),
        ("start_hour"   , 9),
        ("start_min"    , 20),
        ("eod_hour"     , 15),
        ("eod_min"      , 15),
        ("verbose"      , True),
    )

    def __init__(self):
        self.order          = None
        self.entry_price    = None
        self.sl_price       = None
        self.trail_sl       = None
        self.target_price   = None
        self.direction      = None
        self.status         = "watching"   # watching|pending|in_position|done
        self.entry_level    = None
        self.highest        = None
        self.lowest         = None

        self.traded_today   = False
        self.current_day    = None

        # Trade log
        self.trade_log      = []
        self.bar_count      = 0

    # ── Day reset ─────────────────────────────────────────────────────────────
    def _check_day_reset(self):
        dt = self.data.datetime.datetime(0)
        day = dt.date()
        if self.current_day != day:
            self.current_day  = day
            self.traded_today = False
            if self.status != "in_position":
                self.status = "watching"
                self.direction    = None
                self.entry_level  = None
                self.entry_price  = None

    # ── Time checks ───────────────────────────────────────────────────────────
    def _in_trading_window(self):
        dt = self.data.datetime.datetime(0)
        start = dt.replace(hour=self.p.start_hour, minute=self.p.start_min, second=0)
        eod   = dt.replace(hour=self.p.eod_hour,   minute=self.p.eod_min,   second=0)
        return start <= dt < eod

    def _is_eod(self):
        dt = self.data.datetime.datetime(0)
        return dt.hour > self.p.eod_hour or (
            dt.hour == self.p.eod_hour and dt.minute >= self.p.eod_min
        )

    # ── Pattern detection ─────────────────────────────────────────────────────
    def _check_pattern(self):
        if len(self.data) < 5:
            return

        c1_close = self.data.close[-2]   # 2 bars ago
        c2_close = self.data.close[-1]   # 1 bar ago (last closed)
        support    = self.p.support
        resistance = self.p.resistance

        direction    = None
        entry_level  = None

        if c1_close > resistance and c2_close > c1_close:
            direction   = "BUY"
            entry_level = round(c2_close * (1 - self.p.pullback_pct), 2)

        elif c1_close < support and c2_close < c1_close:
            direction   = "SELL"
            entry_level = round(c2_close * (1 + self.p.pullback_pct), 2)

        if direction:
            self.status      = "pending"
            self.direction   = direction
            self.entry_level = entry_level
            if self.p.verbose:
                dt = self.data.datetime.datetime(0)
                print(f"  📍 {dt.strftime('%Y-%m-%d %H:%M')} | "
                      f"PATTERN {direction} | "
                      f"C1={c1_close:.2f} C2={c2_close:.2f} | "
                      f"Entry≤{entry_level:.2f}")

    # ── Try enter ─────────────────────────────────────────────────────────────
    def _try_enter(self):
        ltp = self.data.close[0]
        direction   = self.direction
        entry_level = self.entry_level

        touched = (direction == "BUY"  and ltp <= entry_level) or \
                  (direction == "SELL" and ltp >= entry_level)

        if not touched:
            return

        # Place order
        if direction == "BUY":
            self.order = self.buy(size=1)
        else:
            self.order = self.sell(size=1)

    # ── Manage position ───────────────────────────────────────────────────────
    def _manage_position(self):
        ltp       = self.data.close[0]
        direction = self.direction

        # Update trail
        if direction == "BUY":
            if self.highest is None or ltp > self.highest:
                self.highest = ltp
            new_trail     = self.highest * (1 - self.p.trail_pct)
            self.trail_sl = max(self.sl_price, new_trail)
        else:
            if self.lowest is None or ltp < self.lowest:
                self.lowest = ltp
            new_trail     = self.lowest * (1 + self.p.trail_pct)
            self.trail_sl = min(self.sl_price, new_trail)

        # Check exits
        hit_target = (direction == "BUY"  and ltp >= self.target_price) or \
                     (direction == "SELL" and ltp <= self.target_price)
        hit_sl     = (direction == "BUY"  and ltp <= self.trail_sl) or \
                     (direction == "SELL" and ltp >= self.trail_sl)

        if hit_target or hit_sl:
            reason = "TARGET" if hit_target else "SL/TRAIL"
            if self.p.verbose:
                dt = self.data.datetime.datetime(0)
                pnl_pct = ((ltp - self.entry_price) / self.entry_price * 100
                           if direction == "BUY"
                           else (self.entry_price - ltp) / self.entry_price * 100)
                print(f"  {'🟢' if hit_target else '🔴'} "
                      f"{dt.strftime('%Y-%m-%d %H:%M')} | "
                      f"EXIT {direction} @ {ltp:.2f} | "
                      f"{reason} | PnL={pnl_pct:+.2f}%")

            if direction == "BUY":
                self.order = self.close()
            else:
                self.order = self.close()

    # ── EOD squareoff ─────────────────────────────────────────────────────────
    def _squareoff(self):
        if self.status == "in_position":
            ltp = self.data.close[0]
            if self.p.verbose:
                dt = self.data.datetime.datetime(0)
                print(f"  ⏰ {dt.strftime('%Y-%m-%d %H:%M')} | "
                      f"EOD SQUAREOFF {self.direction} @ {ltp:.2f}")
            self.order = self.close()

    # ── next() ────────────────────────────────────────────────────────────────
    def next(self):
        self.bar_count += 1
        self._check_day_reset()

        # Skip if order pending
        if self.order:
            return

        # EOD squareoff
        if self._is_eod():
            self._squareoff()
            self.status = "done"
            return

        if not self._in_trading_window():
            return

        if self.traded_today or self.status == "done":
            return

        # State machine
        if self.status == "in_position":
            self._manage_position()

        elif self.status == "pending":
            self._try_enter()

        elif self.status == "watching":
            self._check_pattern()

    # ── Order notifications ───────────────────────────────────────────────────
    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return

        if order.status == order.Completed:
            dt    = self.data.datetime.datetime(0)
            price = order.executed.price

            if order.isbuy() and self.status == "pending":
                self.entry_price  = price
                self.sl_price     = round(price * (1 - self.p.sl_pct), 2)
                self.target_price = round(price * (1 + self.p.target_pct), 2)
                self.trail_sl     = self.sl_price
                self.highest      = price
                self.status       = "in_position"
                self.traded_today = True
                print(f"  ✅ {dt.strftime('%Y-%m-%d %H:%M')} | "
                      f"BUY FILLED @ {price:.2f} | "
                      f"SL={self.sl_price:.2f} | "
                      f"Target={self.target_price:.2f}")

            elif order.issell() and self.status == "pending":
                self.entry_price  = price
                self.sl_price     = round(price * (1 + self.p.sl_pct), 2)
                self.target_price = round(price * (1 - self.p.target_pct), 2)
                self.trail_sl     = self.sl_price
                self.lowest       = price
                self.status       = "in_position"
                self.traded_today = True
                print(f"  ✅ {dt.strftime('%Y-%m-%d %H:%M')} | "
                      f"SELL FILLED @ {price:.2f} | "
                      f"SL={self.sl_price:.2f} | "
                      f"Target={self.target_price:.2f}")

            elif self.status == "in_position":
                # Exit filled
                exit_price = price
                pnl = (exit_price - self.entry_price) * (
                    1 if self.direction == "BUY" else -1
                )
                self.trade_log.append({
                    "date"       : dt.strftime("%Y-%m-%d"),
                    "direction"  : self.direction,
                    "entry"      : self.entry_price,
                    "exit"       : exit_price,
                    "pnl_pts"   : round(pnl, 2),
                    "pnl_pct"   : round(pnl / self.entry_price * 100, 3),
                })
                self.status    = "done"
                self.direction = None

        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            print(f"  ⚠️  Order {order.status}: {order.info}")

        self.order = None

    def notify_trade(self, trade):
        if trade.isclosed:
            print(f"  💼 TRADE CLOSED | PnL={trade.pnl:.2f} | "
                  f"PnL Net={trade.pnlcomm:.2f}")

    def stop(self):
        print(f"\n{'='*60}")
        print(f"  Strategy Results")
        print(f"{'='*60}")
        print(f"  Total bars   : {self.bar_count}")
        print(f"  Total trades : {len(self.trade_log)}")
        if self.trade_log:
            df = pd.DataFrame(self.trade_log)
            wins   = df[df["pnl_pts"] > 0]
            losses = df[df["pnl_pts"] <= 0]
            print(f"  Win trades   : {len(wins)}")
            print(f"  Loss trades  : {len(losses)}")
            win_rate = len(wins) / len(df) * 100 if len(df) > 0 else 0
            print(f"  Win rate     : {win_rate:.1f}%")
            print(f"  Total PnL    : {df['pnl_pts'].sum():.2f} pts")
            print(f"\n  Trade Log:")
            print(df.to_string(index=False))
        print(f"{'='*60}")


# ── Plot ──────────────────────────────────────────────────────────────────────
def save_plot(cerebro, symbol, filename=None):
    if filename is None:
        filename = f"backtest_{symbol}.png"
    try:
        figs = cerebro.plot(
            style  = "candlestick",
            iplot  = False,
            volume = True,
        )
        figs[0][0].savefig(filename, dpi=100, bbox_inches="tight")
        plt.close("all")
        print(f"📈 Chart → {os.path.abspath(filename)}")
    except (IndexError, MemoryError, AttributeError) as e:
        print(f"⚠️  Plot skipped: {type(e).__name__}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  SR Breakout Pullback — Backtest")
    print("=" * 60)
    print(f"  Period   : {FROM_DATE} → {TO_DATE}")
    print(f"  Interval : {INTERVAL}")
    print(f"  Capital  : ₹{INITIAL_CASH:,.0f}")
    print(f"  Target   : {TARGET_PCT*100:.1f}% | SL: {SL_PCT*100:.1f}% | "
          f"Trail: {TRAIL_PCT*100:.1f}%")
    print()

    all_results = {}

    for symbol, cfg in SYMBOLS.items():
        print(f"\n{'─'*60}")
        print(f"  Backtesting: {symbol} ({cfg['exchange']})")
        print(f"  S={cfg['support']}  R={cfg['resistance']}")
        print(f"{'─'*60}")

        df = fetch_data(
            symbol, cfg["exchange"], INTERVAL, FROM_DATE, TO_DATE
        )
        if df is None or df.empty:
            print(f"  ⚠️  Skipping {symbol} — no data")
            continue

        cerebro = bt.Cerebro()
        cerebro.adddata(PandasFeed(dataname=df), name=symbol)

        cerebro.addstrategy(
            SRBreakoutPullback,
            support    = cfg["support"],
            resistance = cfg["resistance"],
            verbose    = True,
        )

        cerebro.broker.setcash(INITIAL_CASH)
        cerebro.broker.setcommission(commission=COMMISSION)

        # Analyzers
        cerebro.addanalyzer(btanalyzers.SharpeRatio,
                            _name="sharpe", riskfreerate=0.065)
        cerebro.addanalyzer(btanalyzers.DrawDown,    _name="drawdown")
        cerebro.addanalyzer(btanalyzers.TradeAnalyzer, _name="trades")
        cerebro.addanalyzer(btanalyzers.Returns,     _name="returns")

        start_val = cerebro.broker.getvalue()
        print(f"  💰 Starting: ₹{start_val:,.2f}")

        results = cerebro.run()
        strat   = results[0]

        end_val = cerebro.broker.getvalue()
        pnl     = end_val - start_val
        ret_pct = pnl / start_val * 100

        # Extract analyzer results safely
        try:
            sharpe = strat.analyzers.sharpe.get_analysis()["sharperatio"]
            sharpe = f"{sharpe:.3f}" if sharpe else "N/A"
        except Exception:
            sharpe = "N/A"

        try:
            dd = strat.analyzers.drawdown.get_analysis()
            max_dd = f"{dd['max']['drawdown']:.2f}%"
        except Exception:
            max_dd = "N/A"

        try:
            ta = strat.analyzers.tradeanalyzer.get_analysis()
            total_trades = ta.get("total", {}).get("total", 0)
        except Exception:
            total_trades = "N/A"

        print(f"\n  📊 {symbol} Results:")
        print(f"     Final Value  : ₹{end_val:>12,.2f}")
        print(f"     P&L          : ₹{pnl:>+12,.2f}  ({ret_pct:+.2f}%)")
        print(f"     Sharpe Ratio : {sharpe}")
        print(f"     Max Drawdown : {max_dd}")
        print(f"     Total Trades : {total_trades}")

        all_results[symbol] = {
            "pnl": pnl, "return_pct": ret_pct,
            "sharpe": sharpe, "max_dd": max_dd,
        }

        save_plot(cerebro, symbol)

    # ── Portfolio Summary ─────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  PORTFOLIO SUMMARY")
    print(f"{'='*60}")
    for sym, res in all_results.items():
        emoji = "🟢" if res["pnl"] >= 0 else "🔴"
        print(f"  {emoji} {sym:<12} | "
              f"P&L: ₹{res['pnl']:>+10,.2f}  ({res['return_pct']:+.2f}%)  | "
              f"Sharpe: {res['sharpe']}  |  MaxDD: {res['max_dd']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()