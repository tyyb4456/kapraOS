import { apiClient } from './client.ts';
import type { Purchase } from '../../types/index.ts';

export async function getPurchases(params?: { supplier_id?: string; status?: string }): Promise<Purchase[]> {
  return apiClient.get<Purchase[]>('/purchases', { params });
}

export interface CreatePurchaseRequest {
  supplier_id: string;
  items: Array<{
    variant_id: string;
    quantity: number;
    unit_cost: number;
  }>;
  invoice_number?: string;
  discount?: number;
  paid_amount?: number;
}

export async function createPurchase(data: CreatePurchaseRequest): Promise<Purchase> {
  return apiClient.post<Purchase>('/purchases', data);
}
