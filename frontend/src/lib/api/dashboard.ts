import { apiClient } from './client.ts';
import type { DashboardSummary } from '../../types/index.ts';

function toNumber(value: unknown): number {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export async function getDashboard(): Promise<DashboardSummary> {
  const raw = await apiClient.get<Record<string, unknown>>('/reports/dashboard');
  return {
    as_of: String(raw.as_of ?? ''),
    today_sales: toNumber(raw.today_sales),
    today_sales_count: toNumber(raw.today_sales_count),
    today_payments_received: toNumber(raw.today_payments_received),
    today_payment_count: toNumber(raw.today_payment_count),
    today_purchases: toNumber(raw.today_purchases),
    today_purchase_count: toNumber(raw.today_purchase_count),
    today_cogs: toNumber(raw.today_cogs),
    today_gross_profit: toNumber(raw.today_gross_profit),
    today_expenses: toNumber(raw.today_expenses),
    today_net_profit: toNumber(raw.today_net_profit),
    receivables_outstanding: toNumber(raw.receivables_outstanding),
    payables_outstanding: toNumber(raw.payables_outstanding),
    inventory_quantity: toNumber(raw.inventory_quantity),
    inventory_estimated_value: toNumber(raw.inventory_estimated_value),
    low_stock_variant_count:
      raw.low_stock_variant_count === null || raw.low_stock_variant_count === undefined
        ? null
        : toNumber(raw.low_stock_variant_count),
    low_stock_available: Boolean(raw.low_stock_available),
    notes: Array.isArray(raw.notes) ? (raw.notes as string[]) : [],
  };
}
