import { apiClient } from '../api/client.ts';

export interface VoiceTokenResponse {
  server_url: string;
  participant_token: string;
  room_name: string;
  thread_id: string;
  agent_name: string;
}

export interface VoiceConfigResponse {
  enabled: boolean;
  agent_name: string;
}

export async function getVoiceConfig(): Promise<VoiceConfigResponse> {
  return apiClient.get<VoiceConfigResponse>('/ai/voice/config');
}

export async function requestVoiceToken(threadId?: string | null): Promise<VoiceTokenResponse> {
  // 201 Created — apiClient treats any 2xx as success.
  return apiClient.post<VoiceTokenResponse>(
    '/ai/voice/token',
    threadId ? { thread_id: threadId } : {},
  );
}
