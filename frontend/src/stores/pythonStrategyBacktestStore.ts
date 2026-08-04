import { create } from 'zustand';

export interface TradeRecord {
  trade_id: number
  symbol: string
  date?: string
  day?: string
  entry_time: string
  exit_time: string
  direction: string
  action?: string
  option_type?: string
  size: number
  entry_price: number
  exit_price: number
  pnl_pts?: number
  result?: 'WIN' | 'LOSS'
  holding_time?: string
  pnl: number
  return_pct: number
}

export interface CandleData {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

export interface SignalMarker {
  time: string
  type: string
  label?: string
  price: number
  symbol: string
}

export interface PriceChartData {
  candles: CandleData[]
  signals: SignalMarker[]
}

export interface PortfolioBreakdownItem {
  symbol: string
  trades: number
  pnl: number
  total_return_pct: number
  max_drawdown_pct: number
}

export interface PythonStrategyBacktestResult {
  status: string
  symbol: string
  symbols?: string[]
  metrics: Record<string, any>
  optimization: Record<string, any>
  tearsheet_url: string
  parameters: Record<string, any>
  equity_curve?: { date: string; value: number }[]
  drawdown_curve?: { date: string; drawdown: number }[]
  trades?: TradeRecord[]
  price_charts?: Record<string, PriceChartData>
  portfolio_breakdown?: PortfolioBreakdownItem[]
}

interface PythonStrategyBacktestState {
  result: PythonStrategyBacktestResult | null;
  isLoading: boolean;
  error: string | null;
  setResult: (result: PythonStrategyBacktestResult) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  clearResult: () => void;
}

export const usePythonStrategyBacktestStore = create<PythonStrategyBacktestState>((set) => ({
  result: null,
  isLoading: false,
  error: null,
  setResult: (result) => set({ result, error: null }),
  setLoading: (isLoading) => set({ isLoading }),
  setError: (error) => set({ error }),
  clearResult: () => set({ result: null, error: null }),
}));
