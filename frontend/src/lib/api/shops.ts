import { apiClient } from './client.ts';
import type { ShopSettings, UpdateShopSettingsRequest } from '../../types/index.ts';

export async function getShopSettings(): Promise<ShopSettings> {
  return apiClient.get<ShopSettings>('/shops/me');
}

export async function updateShopSettings(
  data: UpdateShopSettingsRequest,
): Promise<ShopSettings> {
  return apiClient.patch<ShopSettings>('/shops/me', data);
}
