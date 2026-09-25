import { apiClient } from './client.ts';
import type { Sale, PaymentMethod } from '../../types/index.ts';

export async function getSales(params?: {
  customer_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<Sale[]> {
  return apiClient.get<Sale[]>('/sales', { params });
}

export interface CreateSaleRequest {
  items: Array<{
    variant_id: string;
    quantity: number;
    unit_price: number;
    discount?: number;
  }>;
  customer_id?: string;
  invoice_number?: string;
  discount?: number;
  payments?: Array<{
    amount: number;
    method: PaymentMethod;
    reference?: string;
  }>;
}

export async function createSale(data: CreateSaleRequest): Promise<Sale> {
  return apiClient.post<Sale>('/sales', data);
}

export async function getSale(saleId: string): Promise<Sale> {
  return apiClient.get<Sale>(`/sales/${saleId}`);
}

export interface UpdateSaleRequest {
  customer_id?: string | null;
  invoice_number?: string | null;
  discount?: number;
  items?: Array<{
    variant_id: string;
    quantity: number;
    unit_price: number;
    discount?: number;
  }>;
}

export async function updateSale(saleId: string, data: UpdateSaleRequest): Promise<Sale> {
  return apiClient.patch<Sale>(`/sales/${saleId}`, data);
}

export async function deleteSale(saleId: string): Promise<void> {
  await apiClient.delete(`/sales/${saleId}`);
}
