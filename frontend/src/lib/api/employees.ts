import { apiClient } from './client.ts';
import type {
  AcceptInvitationResult,
  EmployeeInvitation,
  EmployeeInvitationCreated,
  ShopEmployee,
} from '../../types/index.ts';

export async function createInvitation(data: {
  email: string;
  role?: 'cashier' | 'salesman';
}): Promise<EmployeeInvitationCreated> {
  return apiClient.post<EmployeeInvitationCreated>('/employees/invitations', data);
}

export async function listInvitations(): Promise<EmployeeInvitation[]> {
  return apiClient.get<EmployeeInvitation[]>('/employees/invitations');
}

export async function revokeInvitation(invitationId: string): Promise<void> {
  await apiClient.delete<void>(`/employees/invitations/${invitationId}`);
}

export async function acceptInvitation(token: string): Promise<AcceptInvitationResult> {
  return apiClient.post<AcceptInvitationResult>('/employees/invitations/accept', { token });
}

export async function listEmployees(): Promise<ShopEmployee[]> {
  return apiClient.get<ShopEmployee[]>('/employees');
}

export async function revokeEmployee(userId: string): Promise<void> {
  await apiClient.delete<void>(`/employees/${userId}`);
}
