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

export async function getPurchase(purchaseId: string): Promise<Purchase> {
  return apiClient.get<Purchase>(`/purchases/${purchaseId}`);
}

export interface UpdatePurchaseRequest {
  supplier_id?: string;
  invoice_number?: string | null;
  discount?: number;
  items?: Array<{
    variant_id: string;
    quantity: number;
    unit_cost: number;
  }>;
}

export async function updatePurchase(purchaseId: string, data: UpdatePurchaseRequest): Promise<Purchase> {
  return apiClient.patch<Purchase>(`/purchases/${purchaseId}`, data);
}

export async function deletePurchase(purchaseId: string): Promise<void> {
  await apiClient.delete(`/purchases/${purchaseId}`);
}
