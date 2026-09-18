import { apiClient } from './client.ts';
import type { Customer, KhataEntry } from '../../types/index.ts';

export async function getCustomers(): Promise<Customer[]> {
  return apiClient.get<Customer[]>('/customers');
}

export async function getCustomerKhata(customerId: string): Promise<KhataEntry[]> {
  return apiClient.get<KhataEntry[]>(`/customers/${customerId}/khata`);
}

export interface CreateCustomerRequest {
  name: string;
  phone?: string;
  email?: string;
  credit_limit?: number;
}

export async function createCustomer(data: CreateCustomerRequest): Promise<Customer> {
  return apiClient.post<Customer>('/customers', data);
}
