import { apiClient } from './client.ts';
import type { FinancialSummary } from '../../types/index.ts';

export type FinancialSummaryPeriod =
  | 'today'
  | 'this_week'
  | 'this_month'
  | 'this_year'
  | 'all';

function toNumber(value: unknown): number {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export async function getFinancialSummary(
  period: FinancialSummaryPeriod | string = 'this_month',
): Promise<FinancialSummary> {
  const raw = await apiClient.get<Record<string, unknown>>(
    '/reports/financial-summary',
    { params: { period } },
  );
  return {
    period_start: String(raw.period_start ?? raw.period_end ?? ''),
    period_end: String(raw.period_end ?? raw.period_start ?? ''),
    total_sales: toNumber(raw.total_sales),
    total_cogs: toNumber(raw.total_cogs),
    gross_profit: toNumber(raw.gross_profit),
    total_expenses: toNumber(raw.total_expenses),
    net_profit: toNumber(raw.net_profit),
    receivables: toNumber(raw.receivables),
    payables: toNumber(raw.payables),
  };
}
