import { apiClient } from './client.ts';
import type {
  OpeningInventoryBatch,
  OpeningInventoryBatchDetail,
  OpeningInventoryLineInput,
  OpeningInventoryPreview,
  OpeningInventorySource,
  PostOpeningInventoryRequest,
} from '../../types/index.ts';

/** Validate proposed opening lines without writing anything. */
export async function previewOpeningInventory(
  lines: OpeningInventoryLineInput[],
): Promise<OpeningInventoryPreview> {
  return apiClient.post<OpeningInventoryPreview>('/inventory/opening/preview', { lines });
}

/**
 * Post an opening inventory batch. `idempotency_key` is required: keep the
 * same key while retrying the same basket so a retried submission returns the
 * already-posted batch instead of creating a second one.
 */
export async function postOpeningInventory(
  payload: PostOpeningInventoryRequest,
): Promise<OpeningInventoryBatchDetail> {
  return apiClient.post<OpeningInventoryBatchDetail>('/inventory/opening', payload);
}

export async function getOpeningInventoryBatches(params?: {
  source?: OpeningInventorySource;
  limit?: number;
  offset?: number;
}): Promise<OpeningInventoryBatch[]> {
  return apiClient.get<OpeningInventoryBatch[]>('/inventory/opening', { params });
}

export async function getOpeningInventoryBatch(batchId: string): Promise<OpeningInventoryBatchDetail> {
  return apiClient.get<OpeningInventoryBatchDetail>(`/inventory/opening/${batchId}`);
}
