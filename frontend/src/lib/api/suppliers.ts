import { apiClient } from './client.ts';
import type { Supplier, KhataEntry } from '../../types/index.ts';

export async function getSuppliers(): Promise<Supplier[]> {
  return apiClient.get<Supplier[]>('/suppliers');
}

export async function getSupplierKhata(supplierId: string): Promise<KhataEntry[]> {
  return apiClient.get<KhataEntry[]>(`/suppliers/${supplierId}/khata`);
}

export interface CreateSupplierRequest {
  name: string;
  contact_person?: string;
  phone?: string;
  email?: string;
}

export async function createSupplier(data: CreateSupplierRequest): Promise<Supplier> {
  return apiClient.post<Supplier>('/suppliers', data);
}
