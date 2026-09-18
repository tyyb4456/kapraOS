import { apiClient } from './client.ts';
import type { AuthMeResponse } from '../../types/index.ts';

export async function getAuthMe(): Promise<AuthMeResponse> {
  return apiClient.get<AuthMeResponse>('/auth/me');
}

export async function syncAuth(data?: { name?: string; email?: string; shop_name?: string }): Promise<AuthMeResponse> {
  return apiClient.post<AuthMeResponse>('/auth/sync', data);
}

