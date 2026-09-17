import { apiClient } from './client.ts';
import type { Purchase } from '../../types/index.ts';

export async function getPurchases(params?: { supplier_id?: string; status?: string }): Promise<Purchase[]> {
  return apiClient.get<Purchase[]>('/purchases', { params });
}
