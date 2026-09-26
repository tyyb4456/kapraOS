import { apiClient } from './client.ts';

export interface AiPendingAction {
  name: string;
  args: Record<string, unknown>;
  allowed_decisions: string[];
}

export type AiChatStatus = 'done' | 'paused';

export interface AiChatResponse {
  status: AiChatStatus;
  thread_id: string;
  reply?: string;
  interrupts?: AiPendingAction[];
}

export interface AiHitlDecision {
  type: 'approve' | 'edit' | 'reject' | 'respond';
  message?: string;
  edited_action?: { name: string; args: Record<string, unknown> };
}

export async function sendAiChat(message: string, threadId?: string | null): Promise<AiChatResponse> {
  return apiClient.post<AiChatResponse>('/ai/chat', {
    message,
    ...(threadId ? { thread_id: threadId } : {}),
  });
}

export async function resumeAiChat(threadId: string, decisions: AiHitlDecision[]): Promise<AiChatResponse> {
  return apiClient.post<AiChatResponse>('/ai/chat/resume', {
    thread_id: threadId,
    decisions,
  });
}
