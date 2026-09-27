import { apiClient } from './client.ts';
import type { DashboardSummary } from '../../types/index.ts';

function toNumber(value: unknown): number {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export async function getDashboard(): Promise<DashboardSummary> {
  const raw = await apiClient.get<Record<string, unknown>>('/reports/dashboard');
  const gross = toNumber(raw.today_sales);
  const returnsTotal = toNumber(raw.today_returns_total);
  // Backend sends today_net_sales; fall back to gross - returns for old backends.
  const netRaw = raw.today_net_sales;
  const net = netRaw === null || netRaw === undefined ? gross - returnsTotal : toNumber(netRaw);
  const purchGross = toNumber(raw.today_purchases);
  const purchRet = toNumber(raw.today_purchase_returns_total);
  const purchNetRaw = raw.today_net_purchases;
  const purchNet =
    purchNetRaw === null || purchNetRaw === undefined ? purchGross - purchRet : toNumber(purchNetRaw);
  const payGross = toNumber(raw.today_payments_received);
  const cashBack = toNumber(raw.today_cash_refunds_total);
  const arReduced = toNumber(raw.today_ar_reduction_total);
  const payNetRaw = raw.today_net_payments_received;
  const payNet =
    payNetRaw === null || payNetRaw === undefined ? payGross - cashBack : toNumber(payNetRaw);
  return {
    as_of: String(raw.as_of ?? ''),
    today_sales: gross,
    today_sales_count: toNumber(raw.today_sales_count),
    today_returns_total: returnsTotal,
    today_returns_count: toNumber(raw.today_returns_count),
    today_net_sales: net,
    today_payments_received: payGross,
    today_payment_count: toNumber(raw.today_payment_count),
    today_cash_refunds_total: cashBack,
    today_ar_reduction_total: arReduced,
    today_net_payments_received: payNet,
    today_purchases: purchGross,
    today_purchase_count: toNumber(raw.today_purchase_count),
    today_purchase_returns_total: purchRet,
    today_purchase_returns_count: toNumber(raw.today_purchase_returns_count),
    today_net_purchases: purchNet,
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
