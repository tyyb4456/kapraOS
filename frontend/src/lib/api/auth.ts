import { apiClient } from './client.ts';
import type { AuthMeResponse } from '../../types/index.ts';

export async function getAuthMe(): Promise<AuthMeResponse> {
  return apiClient.get<AuthMeResponse>('/auth/me');
}
