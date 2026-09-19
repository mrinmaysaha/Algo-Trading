import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Sanitize a value for CSV export to prevent formula injection.
 * Prefixes dangerous characters (=, +, -, @) with a single quote.
 */
export function sanitizeCSV(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return ''
  const str = String(value)
  // Prefix dangerous formula characters with a single quote
  if (/^[=+\-@]/.test(str)) {
    return `'${str}`
  }
  // Escape quotes and wrap in quotes if contains comma
  if (str.includes(',') || str.includes('"') || str.includes('\n')) {
    return `"${str.replace(/"/g, '""')}"`
  }
  return str
}

/**
 * Returns a currency formatter bound to the active broker.
 * - deltaexchange → USD ($)
 * - all other brokers  → INR (₹)
 */
export function makeFormatCurrency(broker?: string | null): (value: number) => string {
  const b = (broker || '').toLowerCase()
  const isUSD = b === 'deltaexchange' || b === 'delta' || b.includes('crypto')
  return (value: number) =>
    isUSD
      ? new Intl.NumberFormat('en-US', {
          style: 'currency',
          currency: 'USD',
          minimumFractionDigits: 2,
        }).format(value)
      : new Intl.NumberFormat('en-IN', {
          style: 'currency',
          currency: 'INR',
          minimumFractionDigits: 2,
        }).format(value)
}

/**
 * Returns the contract multiplier for trade value and P&L calculations.
 * - MCX GOLD and GOLDM options/futures: price is quoted per 10g while qty is in grams -> multiplier = 0.1
 * - Crypto (Delta Exchange): BTC = 0.001, ETH = 0.01, SOL = 0.1
 * - Custom lot_size (e.g. Crypto ETHUSD.P): returns lot_size
 * - Default: 1.0
 */
export function getContractMultiplier(
  symbol?: string,
  exchange?: string,
  lotSize?: number
): number {
  if (!symbol && !exchange) return 1.0
  const exUpper = exchange ? exchange.toUpperCase() : ''
  const symUpper = symbol ? symbol.toUpperCase() : ''

  if (lotSize !== undefined && lotSize !== null && lotSize > 0 && lotSize !== 1) {
    return lotSize
  }

  if (
    exUpper === 'CRYPTO' ||
    exUpper === 'DELTA' ||
    symUpper.includes('BTC') ||
    symUpper.includes('ETH') ||
    symUpper.includes('SOL')
  ) {
    if (symUpper.includes('BTC')) return 0.001
    if (symUpper.includes('ETH')) return 0.01
    if (symUpper.includes('SOL')) return 0.1
    if (symUpper.includes('XRP')) return 1.0
  }

  if (exUpper === 'MCX') {
    if (
      (symUpper.startsWith('GOLDM') || symUpper.startsWith('GOLD')) &&
      !symUpper.startsWith('GOLDGUINEA') &&
      !symUpper.startsWith('GOLDPETAL')
    ) {
      return 0.1
    }
    if (symUpper.startsWith('COTTONCNDY') || symUpper.startsWith('COTTON')) {
      return 0.5
    }
  }

  if (lotSize !== undefined && lotSize !== null && lotSize > 0) {
    return lotSize
  }

  return 1.0
}

/**
 * Formats order/position/trade quantity with both contract count and underlying coin size for crypto.
 */
export function formatQuantityWithMultiplier(
  quantity: number | string,
  symbol?: string,
  exchange?: string,
  lotSize?: number
): { contracts: number; underlying?: string } {
  const qty = Number(quantity) || 0
  const multiplier = getContractMultiplier(symbol, exchange, lotSize)
  if (multiplier === 1.0 || !symbol) {
    return { contracts: qty }
  }
  const symUpper = symbol.toUpperCase()
  let unit = ''
  if (symUpper.includes('BTC')) unit = 'BTC'
  else if (symUpper.includes('ETH')) unit = 'ETH'
  else if (symUpper.includes('SOL')) unit = 'SOL'

  const underlyingVal = qty * multiplier
  const precision = multiplier <= 0.001 ? 4 : multiplier <= 0.01 ? 3 : 2
  const formattedUnderlying = Number.isInteger(underlyingVal)
    ? underlyingVal.toString()
    : parseFloat(underlyingVal.toFixed(precision)).toString()

  return {
    contracts: qty,
    underlying: unit ? `${formattedUnderlying} ${unit}` : `${formattedUnderlying}`,
  }
}

