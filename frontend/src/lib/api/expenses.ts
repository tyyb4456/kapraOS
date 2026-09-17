import { apiClient } from './client.ts';
import type { Expense } from '../../types/index.ts';

export async function getExpenses(): Promise<Expense[]> {
  return apiClient.get<Expense[]>('/expenses');
}
