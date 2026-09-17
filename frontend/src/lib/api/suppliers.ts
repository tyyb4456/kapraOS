import { apiClient } from './client.ts';
import type { Supplier, KhataEntry } from '../../types/index.ts';

export async function getSuppliers(): Promise<Supplier[]> {
  return apiClient.get<Supplier[]>('/suppliers');
}

export async function getSupplierKhata(supplierId: string): Promise<KhataEntry[]> {
  return apiClient.get<KhataEntry[]>(`/suppliers/${supplierId}/khata`);
}
