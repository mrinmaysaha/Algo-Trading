import { webClient } from './client'

export interface OptionRecommendation {
  symbol: string
  expiry: string
  strike: number
  strike_type: string
  lot_size: number
  estimated_delta: number
  opt_entry: number
  opt_sl: number
  opt_tp1: number
  opt_tp2: number
  live_ltp?: number
}

export interface ScannerSignal {
  signal_id: string
  symbol: string
  setup_type: 'INTRADAY' | 'SWING'
  direction: 'BUY' | 'SELL'
  timeframe: string
  spot_price: number
  sl: number
  tp1: number
  tp2: number
  rsi: number
  adx: number
  volume_surge: number
  fo_eligible: boolean
  option_recommendation: OptionRecommendation | null
  created_at: string
  timestamp: string
}

export interface ScannerSignalsResponse {
  status: 'success' | 'warning' | 'error'
  last_updated: string | null
  is_scanning: boolean
  count: number
  signals: ScannerSignal[]
  message?: string
}

export interface Execute1ClickPayload {
  symbol: string
  exchange: string
  quantity: number
  order_type?: string
  price_type?: string
  product?: string
  signal_data?: Record<string, unknown>
}

export interface Execute1ClickResponse {
  status: 'success' | 'error'
  message?: string
  broker_response?: Record<string, unknown>
}

export const scannerApi = {
  /**
   * Fetch current scanned breakout signals from the backend cache
   */
  getSignals: async (
    filter: 'ALL' | 'INTRADAY' | 'SWING' | 'OPTIONS' = 'ALL'
  ): Promise<ScannerSignalsResponse> => {
    const response = await webClient.get<ScannerSignalsResponse>('/api/scanner/signals', {
      params: { filter },
    })
    return response.data
  },

  /**
   * Triggers an asynchronous multi-threaded universe scan
   */
  triggerScan: async (): Promise<{ status: string; message: string }> => {
    const response = await webClient.post<{ status: string; message: string }>('/api/scanner/run')
    return response.data
  },

  /**
   * Execute 1-Click order routed through OpenAlgo
   */
  execute1Click: async (payload: Execute1ClickPayload): Promise<Execute1ClickResponse> => {
    const response = await webClient.post<Execute1ClickResponse>(
      '/api/scanner/execute_1click',
      payload
    )
    return response.data
  },
}
