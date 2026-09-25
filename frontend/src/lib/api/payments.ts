import { apiClient } from './client.ts';

export async function voidPayment(paymentId: string): Promise<void> {
  await apiClient.delete(`/payments/${paymentId}`);
}
