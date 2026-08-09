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
  const isUSD = broker === 'deltaexchange'
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
 * - Custom lot_size (e.g. Crypto ETHUSD.P): returns lot_size
 * - Default: 1.0
 */
export function getContractMultiplier(
  symbol?: string,
  exchange?: string,
  lotSize?: number
): number {
  if (!symbol || !exchange) return 1.0
  const exUpper = exchange.toUpperCase()
  const symUpper = symbol.toUpperCase()

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

  if (lotSize !== undefined && lotSize !== null && lotSize > 0 && lotSize !== 1) {
    return lotSize
  }

  return 1.0
}

