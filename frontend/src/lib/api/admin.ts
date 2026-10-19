import { apiClient } from './client.ts';
import type {
  AdminHealth,
  AdminOverview,
  AdminShopDetailResponse,
  AdminShopListResponse,
  AdminUserDetailResponse,
  AdminUserListResponse,
} from '../../types/index.ts';

export type AdminListParams = {
  search?: string;
  limit?: number;
  offset?: number;
  role?: 'owner' | 'staff';
};

export async function getAdminOverview(): Promise<AdminOverview> {
  return apiClient.get<AdminOverview>('/admin/overview');
}

export async function getAdminHealth(): Promise<AdminHealth> {
  return apiClient.get<AdminHealth>('/admin/health');
}

export async function getAdminUsers(
  params?: AdminListParams,
): Promise<AdminUserListResponse> {
  return apiClient.get<AdminUserListResponse>('/admin/users', { params });
}

export async function getAdminUser(userId: string): Promise<AdminUserDetailResponse> {
  return apiClient.get<AdminUserDetailResponse>(`/admin/users/${userId}`);
}

export async function getAdminShops(
  params?: AdminListParams,
): Promise<AdminShopListResponse> {
  return apiClient.get<AdminShopListResponse>('/admin/shops', { params });
}

export async function getAdminShop(shopId: string): Promise<AdminShopDetailResponse> {
  return apiClient.get<AdminShopDetailResponse>(`/admin/shops/${shopId}`);
}
