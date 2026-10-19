import type { AuthMeResponse } from '../types/index.ts';

/**
 * An identity is usable when it can drive the app:
 *
 * - A regular user must have a real shop (`shop_id` non-null).
 * - A platform administrator may have no shop at all; /admin is the only
 *   surface they see, and the shop UI is never rendered for them.
 * - `pending_sync` is always a placeholder, never a real tenant.
 */
export function isUsableIdentity(
  meData: Pick<AuthMeResponse, 'shop_id'> & { is_platform_admin?: boolean },
): boolean {
  if (meData.shop_id === 'pending_sync') return false;
  return Boolean(meData.shop_id) || Boolean(meData.is_platform_admin);
}
