import { apiClient } from './client.ts';
import type { Customer, KhataEntry, PaymentMethod } from '../../types/index.ts';

export async function getCustomers(): Promise<Customer[]> {
  return apiClient.get<Customer[]>('/customers');
}

export interface StatementEntryResponse {
  entry_type: string;
  date: string;
  reference?: string | null;
  amount: number | string;
  debit: number | string;
  credit: number | string;
  running_balance: number | string;
  sale_id?: string | null;
  payment_id?: string | null;
  invoice_number?: string | null;
  payment_method?: string | null;
}

export interface CustomerStatementResponse {
  customer_id: string;
  opening_balance: number | string;
  closing_balance: number | string;
  total_entries: number;
  entries: StatementEntryResponse[];
}

export async function getCustomerKhata(customerId: string): Promise<KhataEntry[]> {
  const data = await apiClient.get<CustomerStatementResponse | KhataEntry[]>(
    `/customers/${customerId}/statement`
  );

  const rawList: any[] = Array.isArray(data)
    ? data
    : Array.isArray(data?.entries)
    ? data.entries
    : [];

  return rawList.map((entry: any, index: number) => ({
    id: entry.payment_id || entry.sale_id || entry.id || `entry-${index}`,
    payment_id: entry.payment_id ?? null,
    sale_id: entry.sale_id ?? null,
    party_id: customerId,
    party_name: '',
    entry_date: entry.entry_date || entry.date,
    entry_type: (entry.entry_type || (Number(entry.debit) > 0 ? 'sale' : 'payment')).toLowerCase(),
    debit: typeof entry.debit === 'string' ? parseFloat(entry.debit) : Number(entry.debit) || 0,
    credit: typeof entry.credit === 'string' ? parseFloat(entry.credit) : Number(entry.credit) || 0,
    balance_after:
      typeof entry.balance_after === 'string'
        ? parseFloat(entry.balance_after)
        : typeof entry.running_balance === 'string'
        ? parseFloat(entry.running_balance)
        : (entry.balance_after ?? entry.running_balance ?? 0),
    reference: entry.reference || entry.invoice_number || null,
    notes:
      entry.notes ||
      (entry.invoice_number ? `Invoice #${entry.invoice_number}` : undefined),
  }));
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

export async function getCustomer(customerId: string): Promise<Customer> {
  return apiClient.get<Customer>(`/customers/${customerId}`);
}

export interface UpdateCustomerRequest {
  name?: string;
  phone?: string | null;
  email?: string | null;
  credit_limit?: number | null;
  address?: string | null;
  notes?: string | null;
}

export async function updateCustomer(customerId: string, data: UpdateCustomerRequest): Promise<Customer> {
  return apiClient.patch<Customer>(`/customers/${customerId}`, data);
}

export async function deleteCustomer(customerId: string): Promise<void> {
  await apiClient.delete(`/customers/${customerId}`);
}

export interface RecordCustomerPaymentRequest {
  amount: number;
  method: PaymentMethod;
  sale_id?: string | null;
  reference?: string | null;
}

export async function recordCustomerPayment(
  customerId: string,
  data: RecordCustomerPaymentRequest
): Promise<any> {
  return apiClient.post(`/customers/${customerId}/payments`, data);
}
