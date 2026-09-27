import { apiClient } from './client.ts';
import type {
  CreatePurchaseReturnResponse,
  CreateSaleReturnResponse,
  PurchaseDetail,
  PurchaseReturn,
  SaleDetail,
  SaleReturn,
} from '../../types/index.ts';

export interface SaleReturnLineInput {
  sale_item_id: string;
  quantity: number | string;
}

export async function getSaleDetail(saleId: string): Promise<SaleDetail> {
  return apiClient.get<SaleDetail>(`/sales/${saleId}`);
}

export async function getSaleReturns(saleId: string): Promise<SaleReturn[]> {
  return apiClient.get<SaleReturn[]>(`/sales/${saleId}/returns`);
}

export async function createSaleReturn(
  saleId: string,
  data: { lines: SaleReturnLineInput[]; notes?: string | null },
): Promise<CreateSaleReturnResponse> {
  return apiClient.post<CreateSaleReturnResponse>(`/sales/${saleId}/returns`, data);
}

export interface PurchaseReturnLineInput {
  purchase_item_id: string;
  quantity: number | string;
}

export async function getPurchaseDetail(purchaseId: string): Promise<PurchaseDetail> {
  return apiClient.get<PurchaseDetail>(`/purchases/${purchaseId}`);
}

export async function getPurchaseReturns(purchaseId: string): Promise<PurchaseReturn[]> {
  return apiClient.get<PurchaseReturn[]>(`/purchases/${purchaseId}/returns`);
}

export async function createPurchaseReturn(
  purchaseId: string,
  data: { lines: PurchaseReturnLineInput[]; notes?: string | null },
): Promise<CreatePurchaseReturnResponse> {
  return apiClient.post<CreatePurchaseReturnResponse>(`/purchases/${purchaseId}/returns`, data);
}
