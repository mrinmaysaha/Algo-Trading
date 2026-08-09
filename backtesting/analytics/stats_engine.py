# backtesting/analytics/stats_engine.py
"""
Performance Analytics, StockMock/AlgoTest Monthly PnL Matrix, Run Manifests & Disclosures.
"""
import hashlib
import json
import platform
from datetime import datetime, date, time
from typing import Dict, List, Any
import numpy as np
import pandas as pd


def sanitize_json_types(obj: Any) -> Any:
    """Recursively converts NumPy, Pandas, Datetime, Date, and Time types to native Python types for clean JSON serialization."""
    if isinstance(obj, dict):
        return {
            (str(k) if isinstance(k, (np.integer, np.floating, pd.Timestamp, datetime, date, time)) else sanitize_json_types(k)): sanitize_json_types(v)
            for k, v in obj.items()
        }
    elif isinstance(obj, (list, tuple, set)):
        return [sanitize_json_types(v) for v in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return sanitize_json_types(obj.tolist())
    elif isinstance(obj, (pd.Timestamp, datetime, date, time)):
        return str(obj)
    elif isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    return obj


def build_run_manifest(strategy_path: str, config: Dict[str, Any], data_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Generates an audit trail manifest for reproducibility."""
    code_hash = "INLINE_SCRIPT"
    if strategy_path and hasattr(strategy_path, "read"):
        code_hash = hashlib.sha256(strategy_path.read().encode()).hexdigest()[:16]
    elif isinstance(strategy_path, str) and len(strategy_path) < 260 and "\n" not in strategy_path:
        try:
            with open(strategy_path, "rb") as f:
                code_hash = hashlib.sha256(f.read()).hexdigest()[:16]
        except Exception:
            pass

    config_str = json.dumps(config, sort_keys=True, default=str)
    run_id = hashlib.sha256(f"{code_hash}{config_str}{json.dumps(data_meta)}".encode()).hexdigest()[:12]

    return {
        "run_id": run_id,
        "strategy_code_hash": code_hash,
        "config_snapshot": config,
        "data_snapshot": data_meta,
        "engine_version": "2.0.0-PROD",
        "python_version": platform.python_version(),
        "generated_at": datetime.utcnow().isoformat()
    }


def get_assumptions_disclosure(slippage_pts: float) -> Dict[str, str]:
    """Generates transparency disclosure block for the backtest report."""
    return {
        "pricing_model": "Black-Scholes-Merton (NSE/BSE Spot Indices) / Black-76 (MCX Commodities)",
        "iv_source": "Model-based default IV (Static proxy per asset class)",
        "dte_calculation": "Exact fractional DTE calculated down to market close (15:30 IST)",
        "lot_size_source": "Versioned SEBI / Exchange lot size history table",
        "slippage_model": f"Flat {slippage_pts} points per order fill",
        "tax_schedule_version": "NSE STT (0.10%) / MCX CTT (0.05%) Schedule",
        "confidence_grade": "A- (Model-based premium options backtest with statutory costs)"
    }


class StockMockPnLMatrix:
    """Generates StockMock / AlgoTest Monthly PnL Matrix & Heatmaps."""

    def __init__(self, trades: List[Dict], initial_capital: float = 100000.0):
        self.trades_df = pd.DataFrame(trades)
        self.initial_capital = initial_capital

        if not self.trades_df.empty and "exit_time" in self.trades_df.columns:
            self.trades_df["exit_time"] = pd.to_datetime(self.trades_df["exit_time"])
            self.trades_df["year"] = self.trades_df["exit_time"].dt.year
            self.trades_df["month"] = self.trades_df["exit_time"].dt.month

    def generate_matrix(self) -> pd.DataFrame:
        if self.trades_df.empty:
            return pd.DataFrame()

        grouped = self.trades_df.groupby(["year", "month"])["net_pnl_rs"].sum().unstack(level=1).fillna(0.0)

        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        for m in range(1, 13):
            if m not in grouped.columns:
                grouped[m] = 0.0

        grouped = grouped.reindex(columns=range(1, 13))
        grouped.columns = month_names
        grouped["Total PnL (₹)"] = grouped.sum(axis=1)

        yearly_win_rates = []
        for year in grouped.index:
            y_trades = self.trades_df[self.trades_df["year"] == year]
            wins = float((y_trades["net_pnl_rs"] > 0).sum())
            total = len(y_trades)
            wr = round((wins / total * 100.0), 1) if total > 0 else 0.0
            yearly_win_rates.append(f"{wr}%")

        grouped["Win Rate"] = yearly_win_rates
        grouped["ROI (%)"] = ((grouped["Total PnL (₹)"] / self.initial_capital) * 100.0).round(2)
        grouped.index = [str(y) for y in grouped.index]

        return grouped

    def to_html_heatmap(self) -> str:
        matrix = self.generate_matrix()
        if matrix.empty:
            return "<p>No trade data available for PnL Matrix.</p>"

        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        html = """
        <style>
            .stockmock-table { width: 100%; border-collapse: collapse; font-family: sans-serif; font-size: 13px; text-align: right; }
            .stockmock-table th { background-color: #1e222d; color: #d1d4dc; padding: 10px; border: 1px solid #2a2e39; text-align: center; }
            .stockmock-table td { padding: 10px; border: 1px solid #2a2e39; font-weight: 600; }
            .cell-year { text-align: center; background-color: #1e222d; color: #ffffff; }
            .pos-pnl { background-color: rgba(38, 166, 154, 0.18); color: #26a69a; }
            .neg-pnl { background-color: rgba(239, 83, 80, 0.18); color: #ef5350; }
            .zero-pnl { background-color: #1e222d; color: #787b86; }
            .cell-total { background-color: #2a2e39; font-weight: bold; }
        </style>
        <table class="stockmock-table">
            <thead>
                <tr>
                    <th>Year</th>
                    """ + "".join([f"<th>{m}</th>" for m in months]) + """
                    <th>Total PnL (₹)</th>
                    <th>ROI</th>
                    <th>Win Rate</th>
                </tr>
            </thead>
            <tbody>
        """

        for year, row in matrix.iterrows():
            html += f"<tr><td class='cell-year'>{year}</td>"
            for m in months:
                val = row[m]
                css_class = "pos-pnl" if val > 0 else ("neg-pnl" if val < 0 else "zero-pnl")
                formatted_val = f"₹{val:,.0f}" if val != 0 else "-"
                html += f"<td class='{css_class}'>{formatted_val}</td>"

            tot_pnl = row["Total PnL (₹)"]
            tot_css = "pos-pnl" if tot_pnl > 0 else ("neg-pnl" if tot_pnl < 0 else "zero-pnl")
            html += f"<td class='cell-total {tot_css}'>₹{tot_pnl:,.2f}</td>"
            html += f"<td class='cell-total {tot_css}'>{row['ROI (%)']}%</td>"
            html += f"<td class='cell-total' style='color: #d1d4dc;'>{row['Win Rate']}</td></tr>"

        html += "</tbody></table>"
        return html


class PerformanceAnalytics:
    """Calculates standardized metrics resampled on business days."""

    @staticmethod
    def calculate_metrics(trades: List[Dict], initial_capital: float, start_date: str, end_date: str) -> Dict:
        if not trades:
            return {
                "Total Return [%]": 0.0, "Total PnL [₹]": 0.0, "Total Charges [₹]": 0.0,
                "Max Drawdown [%]": 0.0, "Sharpe Ratio": 0.0, "Sortino Ratio": 0.0,
                "Calmar Ratio": 0.0, "Win Rate [%]": 0.0, "Profit Factor": 0.0,
                "Total Trades": 0, "Winning Trades": 0, "Losing Trades": 0
            }

        df_trades = pd.DataFrame(trades)
        df_trades["exit_time"] = pd.to_datetime(df_trades["exit_time"])
        df_trades = df_trades.sort_values("exit_time").reset_index(drop=True)

        pnl_col = "net_pnl_rs" if "net_pnl_rs" in df_trades.columns else ("pnl" if "pnl" in df_trades.columns else "rupee_pnl")
        charges_col = "charges_rs" if "charges_rs" in df_trades.columns else ("charges" if "charges" in df_trades.columns else None)

        total_trades = int(len(df_trades))
        winning_trades = int((df_trades[pnl_col] > 0).sum())
        losing_trades = int(total_trades - winning_trades)

        gross_profit = float(df_trades[df_trades[pnl_col] > 0][pnl_col].sum()) if winning_trades > 0 else 0.0
        gross_loss = float(abs(df_trades[df_trades[pnl_col] < 0][pnl_col].sum())) if losing_trades > 0 else 0.0
        total_pnl = float(df_trades[pnl_col].sum())
        total_charges = float(df_trades[charges_col].sum()) if (charges_col and charges_col in df_trades.columns) else 0.0

        win_rate = float(round((winning_trades / total_trades) * 100.0, 2)) if total_trades > 0 else 0.0
        profit_factor = float(round(gross_profit / gross_loss, 2)) if gross_loss > 0 else float(round(gross_profit, 2))

        # Equity Curve Resampled strictly on Business Days
        df_trades["date"] = df_trades["exit_time"].dt.floor("D")
        trading_days = pd.bdate_range(start=start_date, end=end_date)
        daily_equity = pd.Series(initial_capital, index=trading_days)

        daily_pnl = df_trades.groupby("date")[pnl_col].sum()
        for d, pnl in daily_pnl.items():
            if d in daily_equity.index:
                daily_equity.loc[d:] += float(pnl)

        daily_returns = daily_equity.pct_change().dropna()

        ret_mean = daily_returns.mean()
        ret_std = daily_returns.std()
        sharpe = float((ret_mean / ret_std) * np.sqrt(252)) if ret_std > 0 else 0.0

        downside_std = daily_returns[daily_returns < 0].std()
        sortino = float((ret_mean / downside_std) * np.sqrt(252)) if downside_std > 0 else 0.0

        peak = daily_equity.cummax()
        drawdown = (daily_equity - peak) / peak
        max_drawdown_pct = abs(float(drawdown.min())) * 100.0

        days_count = max(1, (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days)
        final_equity = float(initial_capital + total_pnl)
        cagr = float((((final_equity / initial_capital) ** (365.0 / days_count)) - 1.0) * 100.0) if final_equity > 0 else 0.0
        calmar = float(cagr / max_drawdown_pct) if max_drawdown_pct > 0 else 0.0

        expectancy = float(round(total_pnl / total_trades, 2)) if total_trades > 0 else 0.0

        return sanitize_json_types({
            "Total Return [%]": float(round((total_pnl / initial_capital) * 100.0, 2)),
            "CAGR [%]": float(round(cagr, 2)),
            "Total PnL [₹]": float(round(total_pnl, 2)),
            "Total Charges [₹]": float(round(total_charges, 2)),
            "Max Drawdown [%]": float(round(max_drawdown_pct, 2)),
            "Sharpe Ratio": float(round(sharpe, 2)),
            "Sortino Ratio": float(round(sortino, 2)),
            "Calmar Ratio": float(round(calmar, 2)),
            "Win Rate [%]": win_rate,
            "Profit Factor": profit_factor,
            "Expectancy": expectancy,
            "Gross Profit": float(round(gross_profit, 2)),
            "Gross Loss": float(round(gross_loss, 2)),
            "Total Trades": total_trades,
            "Winning Trades": winning_trades,
            "Losing Trades": losing_trades,
            "Initial Capital": float(initial_capital),
            "Final Portfolio Value": float(round(final_equity, 2))
        })
