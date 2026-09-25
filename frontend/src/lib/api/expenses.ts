import { apiClient } from './client.ts';
import type { Expense } from '../../types/index.ts';

export async function getExpenses(): Promise<Expense[]> {
  return apiClient.get<Expense[]>('/expenses');
}

export interface CreateExpenseRequest {
  category: string;
  amount: number;
  description?: string | null;
  payment_method: 'cash' | 'bank' | 'card' | 'jazzcash' | 'easypaisa' | 'other';
  expense_date?: string;
}

export async function createExpense(data: CreateExpenseRequest): Promise<Expense> {
  return apiClient.post<Expense>('/expenses', data);
}

export interface UpdateExpenseRequest {
  category?: string;
  amount?: number;
  description?: string | null;
  payment_method?: 'cash' | 'bank' | 'card' | 'jazzcash' | 'easypaisa' | 'other';
  expense_date?: string;
}

export async function updateExpense(expenseId: string, data: UpdateExpenseRequest): Promise<Expense> {
  return apiClient.patch<Expense>(`/expenses/${expenseId}`, data);
}

export async function deleteExpense(expenseId: string): Promise<void> {
  await apiClient.delete(`/expenses/${expenseId}`);
}
