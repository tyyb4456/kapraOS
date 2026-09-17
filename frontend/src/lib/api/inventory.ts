import { apiClient } from './client.ts';
import type { InventoryItem, StockMovement } from '../../types/index.ts';

export async function getInventory(params?: { low_stock?: boolean }): Promise<InventoryItem[]> {
  return apiClient.get<InventoryItem[]>('/inventory', { params });
}

export async function getStockMovements(params?: { variant_id?: string; limit?: number }): Promise<StockMovement[]> {
  return apiClient.get<StockMovement[]>('/inventory/movements', { params });
}
