import { apiClient } from './client.ts';
import type { Supplier, KhataEntry, PaymentMethod } from '../../types/index.ts';

export async function getSuppliers(): Promise<Supplier[]> {
  return apiClient.get<Supplier[]>('/suppliers');
}

export async function getSupplier(supplierId: string): Promise<Supplier> {
  return apiClient.get<Supplier>(`/suppliers/${supplierId}`);
}

export interface SupplierBalance {
  supplier_id: string;
  total_purchases: number;
  total_payments: number;
  total_returns: number;
  outstanding_balance: number;
  number_of_purchases: number;
  number_of_payments: number;
  number_of_returns: number;
  last_purchase_at: string | null;
  last_payment_at: string | null;
  last_return_at: string | null;
}

export async function getSupplierBalance(supplierId: string): Promise<SupplierBalance> {
  const data = await apiClient.get<any>(`/suppliers/${supplierId}/balance`);
  const num = (v: unknown) => (typeof v === 'string' ? parseFloat(v) : Number(v) || 0);
  return {
    supplier_id: data.supplier_id,
    total_purchases: num(data.total_purchases),
    total_payments: num(data.total_payments),
    total_returns: num(data.total_returns),
    outstanding_balance: num(data.outstanding_balance),
    number_of_purchases: data.number_of_purchases ?? 0,
    number_of_payments: data.number_of_payments ?? 0,
    number_of_returns: data.number_of_returns ?? 0,
    last_purchase_at: data.last_purchase_at ?? null,
    last_payment_at: data.last_payment_at ?? null,
    last_return_at: data.last_return_at ?? null,
  };
}

export interface SupplierStatementEntryResponse {
  entry_type: string;
  date: string;
  reference?: string | null;
  amount: number | string;
  debit: number | string;
  credit: number | string;
  running_balance: number | string;
  purchase_id?: string | null;
  payment_id?: string | null;
  return_id?: string | null;
  invoice_number?: string | null;
  payment_method?: string | null;
}

export interface SupplierStatementResponse {
  supplier_id: string;
  opening_balance: number | string;
  closing_balance: number | string;
  total_entries: number;
  entries: SupplierStatementEntryResponse[];
}

export async function getSupplierKhata(supplierId: string): Promise<KhataEntry[]> {
  const data = await apiClient.get<SupplierStatementResponse | KhataEntry[]>(
    `/suppliers/${supplierId}/statement`
  );

  const rawList: any[] = Array.isArray(data)
    ? data
    : Array.isArray(data?.entries)
    ? data.entries
    : [];

  return rawList.map((entry: any, index: number) => ({
    id: entry.return_id || entry.payment_id || entry.purchase_id || entry.id || `entry-${index}`,
    payment_id: entry.payment_id ?? null,
    purchase_id: entry.purchase_id ?? null,
    return_id: entry.return_id ?? null,
    party_id: supplierId,
    party_name: '',
    entry_date: entry.entry_date || entry.date,
    entry_type: (entry.entry_type || (Number(entry.credit) > 0 ? 'purchase' : 'payment')).toLowerCase(),
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
      (entry.invoice_number ? `Bill #${entry.invoice_number}` : undefined),
  }));
}

export interface CreateSupplierRequest {
  name: string;
  phone?: string;
  address?: string;
  notes?: string;
}

export async function createSupplier(data: CreateSupplierRequest): Promise<Supplier> {
  return apiClient.post<Supplier>('/suppliers', data);
}

export interface UpdateSupplierRequest {
  name?: string;
  phone?: string | null;
  address?: string | null;
  notes?: string | null;
}

export async function updateSupplier(supplierId: string, data: UpdateSupplierRequest): Promise<Supplier> {
  return apiClient.patch<Supplier>(`/suppliers/${supplierId}`, data);
}

export async function deleteSupplier(supplierId: string): Promise<void> {
  await apiClient.delete(`/suppliers/${supplierId}`);
}

export interface RecordSupplierPaymentRequest {
  amount: number;
  method: PaymentMethod;
  purchase_id?: string | null;
  reference?: string | null;
}

export async function recordSupplierPayment(
  supplierId: string,
  data: RecordSupplierPaymentRequest
): Promise<any> {
  return apiClient.post(`/suppliers/${supplierId}/payments`, data);
}
