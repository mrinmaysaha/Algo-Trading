import type {
  AddSymbolRequest,
  CreateStrategyRequest,
  Strategy,
  StrategySymbolMapping,
  SymbolSearchResult,
} from '@/types/webhookStrategy'
import type { ApiResponse } from '@/types/trading'
import { webClient } from './client'

export const webhookStrategyApi = {
  /**
   * Get all strategies
   */
  getStrategies: async (): Promise<Strategy[]> => {
    const response = await webClient.get<{ strategies: Strategy[] }>('/webhook-strategy/api/strategies')
    return response.data.strategies || []
  },

  /**
   * Get a single strategy by ID
   */
  getStrategy: async (
    strategyId: number
  ): Promise<{ strategy: Strategy; mappings: StrategySymbolMapping[] }> => {
    const response = await webClient.get<{ strategy: Strategy; mappings: StrategySymbolMapping[] }>(
      `/webhook-strategy/api/strategy/${strategyId}`
    )
    return response.data
  },

  /**
   * Create a new strategy
   */
  createStrategy: async (
    data: CreateStrategyRequest
  ): Promise<ApiResponse<{ strategy_id: number }>> => {
    const response = await webClient.post<ApiResponse<{ strategy_id: number }>>(
      '/webhook-strategy/api/strategy',
      data
    )
    return response.data
  },

  /**
   * Toggle strategy active/inactive
   */
  toggleStrategy: async (strategyId: number): Promise<ApiResponse<{ is_active: boolean }>> => {
    const response = await webClient.post<ApiResponse<{ is_active: boolean }>>(
      `/webhook-strategy/api/strategy/${strategyId}/toggle`
    )
    return response.data
  },

  /**
   * Delete a strategy
   */
  deleteStrategy: async (strategyId: number): Promise<ApiResponse<void>> => {
    const response = await webClient.post<ApiResponse<void>>(`/webhook-strategy/${strategyId}/delete`)
    return response.data
  },

  /**
   * Add a symbol mapping to a strategy
   */
  addSymbolMapping: async (
    strategyId: number,
    data: AddSymbolRequest
  ): Promise<ApiResponse<void>> => {
    const response = await webClient.post<ApiResponse<void>>(
      `/webhook-strategy/${strategyId}/configure`,
      data
    )
    return response.data
  },

  /**
   * Add bulk symbol mappings
   */
  addBulkSymbols: async (
    strategyId: number,
    csvData: string
  ): Promise<ApiResponse<{ added: number; failed: number }>> => {
    const response = await webClient.post<ApiResponse<{ added: number; failed: number }>>(
      `/webhook-strategy/${strategyId}/configure`,
      { symbols: csvData } // Backend expects 'symbols' field
    )
    return response.data
  },

  /**
   * Delete a symbol mapping
   */
  deleteSymbolMapping: async (
    strategyId: number,
    mappingId: number
  ): Promise<ApiResponse<void>> => {
    const response = await webClient.post<ApiResponse<void>>(
      `/webhook-strategy/${strategyId}/symbol/${mappingId}/delete`
    )
    return response.data
  },

  /**
   * Search symbols
   */
  searchSymbols: async (query: string, exchange?: string): Promise<SymbolSearchResult[]> => {
    const params = new URLSearchParams({ q: query })
    if (exchange) {
      params.append('exchange', exchange)
    }
    const response = await webClient.get<{ results: SymbolSearchResult[] }>(
      `/webhook-strategy/search?${params.toString()}`
    )
    return response.data.results || []
  },

  /**
   * Get webhook URL for a strategy
   */
  getWebhookUrl: (webhookId: string): string => {
    const baseUrl = window.location.origin
    return `${baseUrl}/strategy/webhook/${webhookId}`
  },
}
