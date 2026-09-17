import { apiClient } from './client.ts';
import type { Sale } from '../../types/index.ts';

export async function getSales(params?: { customer_id?: string; status?: string }): Promise<Sale[]> {
  return apiClient.get<Sale[]>('/sales', { params });
}
