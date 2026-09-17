import type { UnitOfMeasure } from '../types/index.ts';

/**
 * Format currency in Pakistani Rupees (PKR)
 * Example: 12500 -> "Rs. 12,500", 1250.5 -> "Rs. 1,250.50"
 */
export function formatCurrency(amount: number | null | undefined): string {
  if (amount === null || amount === undefined || isNaN(amount)) {
    return 'Rs. 0';
  }

  const isNegative = amount < 0;
  const absAmount = Math.abs(amount);

  const hasDecimals = absAmount % 1 !== 0;
  const formattedNumber = new Intl.NumberFormat('en-PK', {
    minimumFractionDigits: hasDecimals ? 2 : 0,
    maximumFractionDigits: 2,
  }).format(absAmount);

  return isNegative ? `-Rs. ${formattedNumber}` : `Rs. ${formattedNumber}`;
}

/**
 * Format quantity with respectful unit of measure for fabrics & garments
 * Example: (45.5, "meters") -> "45.50 meters", (12, "pieces") -> "12 pieces"
 */
export function formatQuantity(
  quantity: number | null | undefined,
  unit?: UnitOfMeasure | string | null,
): string {
  if (quantity === null || quantity === undefined || isNaN(quantity)) {
    return `0 ${unit || 'pieces'}`.trim();
  }

  const hasDecimals = quantity % 1 !== 0;
  const formattedNumber = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: hasDecimals ? 2 : 0,
    maximumFractionDigits: 3,
  }).format(quantity);

  return `${formattedNumber} ${unit || 'pieces'}`.trim();
}

/**
 * Format a date string or Date object into standard readable business format
 * Example: "17 Sep 2026" or "17 Sep 2026, 02:45 PM"
 */
export function formatDate(
  dateValue: string | Date | null | undefined,
  includeTime = false,
): string {
  if (!dateValue) return '—';

  const date = typeof dateValue === 'string' ? new Date(dateValue) : dateValue;
  if (isNaN(date.getTime())) return '—';

  const options: Intl.DateTimeFormatOptions = {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    ...(includeTime
      ? {
          hour: '2-digit',
          minute: '2-digit',
          hour12: true,
        }
      : {}),
  };

  return new Intl.DateTimeFormat('en-GB', options).format(date);
}

/**
 * Format short date (e.g., "17/09/2026")
 */
export function formatShortDate(dateValue: string | Date | null | undefined): string {
  if (!dateValue) return '—';
  const date = typeof dateValue === 'string' ? new Date(dateValue) : dateValue;
  if (isNaN(date.getTime())) return '—';

  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  }).format(date);
}
