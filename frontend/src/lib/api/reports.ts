import { apiClient } from './client.ts';
import type { FinancialSummary } from '../../types/index.ts';

export async function getFinancialSummary(): Promise<FinancialSummary> {
  return apiClient.get<FinancialSummary>('/reporting/financial-summary');
}
