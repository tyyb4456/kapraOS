import { apiClient } from './client.ts';
import type { Supplier, KhataEntry, PaymentMethod } from '../../types/index.ts';

export async function getSuppliers(): Promise<Supplier[]> {
  return apiClient.get<Supplier[]>('/suppliers');
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
    id: entry.id || entry.purchase_id || entry.payment_id || `entry-${index}`,
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
