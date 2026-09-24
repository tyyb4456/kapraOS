import { apiClient } from './client.ts';
import type { SalesTrend, TrendBucket } from '../../types/index.ts';

export interface SalesTrendParams {
  start_date?: string;
  end_date?: string;
  granularity?: 'day' | 'week' | 'month' | 'auto';
}

function toNumber(value: unknown): number {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}

function toBucket(raw: Record<string, unknown>): TrendBucket {
  return {
    bucket_start: String(raw.bucket_start ?? ''),
    revenue: toNumber(raw.revenue),
    cogs: toNumber(raw.cogs),
    gross_profit: toNumber(raw.gross_profit),
    expenses: toNumber(raw.expenses),
    net_profit: toNumber(raw.net_profit),
    sales_count: toNumber(raw.sales_count),
    sales_total: toNumber(raw.sales_total),
  };
}

export async function getSalesTrend(params?: SalesTrendParams): Promise<SalesTrend> {
  const raw = await apiClient.get<Record<string, unknown>>('/reports/sales-trend', {
    params: params as Record<string, string | undefined> | undefined,
  });
  const buckets = Array.isArray(raw.buckets)
    ? (raw.buckets as Record<string, unknown>[]).map(toBucket)
    : [];
  return {
    start_date: String(raw.start_date ?? ''),
    end_date: String(raw.end_date ?? ''),
    granularity: String(raw.granularity ?? 'day'),
    buckets,
    total_revenue: toNumber(raw.total_revenue),
    total_cogs: toNumber(raw.total_cogs),
    total_gross_profit: toNumber(raw.total_gross_profit),
    total_expenses: toNumber(raw.total_expenses),
    total_net_profit: toNumber(raw.total_net_profit),
    total_sales_count: toNumber(raw.total_sales_count),
  };
}
