export interface Position {
  symbol: string
  exchange: string
  product: 'MIS' | 'NRML' | 'CNC'
  quantity: number
  average_price: number
  ltp: number
  pnl: number
  pnlpercent: number
  lot_size?: number // contract_value multiplier (e.g. 0.01 for ETHUSD.P)
  today_realized_pnl?: number // Sandbox: today's realized P&L from closed partial trades
  strategy?: string // Originating Strategy Name
}

export interface Order {
  symbol: string
  exchange: string
  action: 'BUY' | 'SELL'
  quantity: number
  price: number
  trigger_price: number
  pricetype: 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'
  product: 'MIS' | 'NRML' | 'CNC'
  orderid: string
  order_status: 'complete' | 'rejected' | 'cancelled' | 'open' | 'pending' | 'trigger pending'
  timestamp: string
  strategy?: string // Originating Strategy Name
}

export interface Trade {
  symbol: string
  exchange: string
  action: 'BUY' | 'SELL'
  quantity: number
  average_price: number
  trade_value: number
  product: string
  orderid: string
  timestamp: string
  strategy?: string // Originating Strategy Name
}

export interface Holding {
  symbol: string
  exchange: string
  quantity: number
  product: string
  pnl: number
  pnlpercent: number
  ltp?: number
  average_price?: number
}

export interface PortfolioStats {
  totalholdingvalue: number
  totalinvvalue: number
  totalprofitandloss: number
  totalpnlpercentage: number
}

// Alias for consistency
export type HoldingsStats = PortfolioStats

export interface MarginData {
  availablecash: number
  collateral: number
  m2munrealized: number
  m2mrealized: number
  utiliseddebits: number
}

export interface OrderStats {
  total_buy_orders: number
  total_sell_orders: number
  total_completed_orders: number
  total_open_orders: number
  total_rejected_orders: number
}

// -----------------------------------------------------------------------------
// GTT (Good Till Triggered)
// -----------------------------------------------------------------------------

export interface GttLeg {
  action: string // "BUY" | "SELL"
  quantity: number
  price: number
  pricetype: string // usually "LIMIT"
  product: string // "MIS" | "NRML" | "CNC"
}

export type GttStatus =
  | 'active'
  | 'triggered'
  | 'disabled'
  | 'expired'
  | 'cancelled'
  | 'rejected'
  | 'deleted'
  | string // broker-specific statuses passed through as-is

export interface GttOrder {
  trigger_id: string
  trigger_type: 'single' | 'two-leg' | string
  status: GttStatus
  symbol: string
  exchange: string
  trigger_prices: number[]
  last_price: number
  legs: GttLeg[]
  created_at?: string
  updated_at?: string
  expires_at?: string
}

export interface PlaceOrderRequest {
  apikey: string
  strategy: string
  exchange: string
  symbol: string
  action: 'BUY' | 'SELL'
  quantity: number
  pricetype?: 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'
  product?: 'MIS' | 'NRML' | 'CNC'
  price?: number
  trigger_price?: number
  disclosed_quantity?: number
}

export interface ApiResponse<T> {
  status: 'success' | 'error' | 'info'
  message?: string
  data?: T
}

export interface StrategyDailyPnLPoint {
  date: string
  pnl: number
}

export interface StrategyPerformanceMetric {
  strategy: string
  timeframe: string
  realized_pnl: number
  unrealized_pnl: number
  total_pnl: number
  today_realized_pnl: number
  open_quantity: number
  total_trades: number
  win_rate: number
  profit_factor: number
  max_drawdown: number
  avg_trade_pnl: number
  active_positions_count: number
  has_activity?: boolean
  legs: any[]
  daily_pnl_history: StrategyDailyPnLPoint[]
}

export interface StrategyAnalyticsResponse {
  status: 'success' | 'error'
  timeframe: string
  days: number
  as_of: string
  portfolio_summary: {
    total_pnl: number
    total_trades: number
    active_strategies_count: number
    total_strategies_count?: number
    winning_strategies_count: number
    losing_strategies_count: number
    top_performer: string
  }
  strategies: Record<string, StrategyPerformanceMetric>
}

