import { describe, expect, it } from 'vitest';
import { isUsableIdentity } from '../identity.ts';

describe('isUsableIdentity', () => {
  it('accepts a regular user with a real shop', () => {
    expect(isUsableIdentity({ shop_id: 'shop-1' })).toBe(true);
    expect(isUsableIdentity({ shop_id: 'shop-1', is_platform_admin: false })).toBe(true);
  });

  it('accepts a platform admin without a shop', () => {
    expect(isUsableIdentity({ shop_id: null, is_platform_admin: true })).toBe(true);
  });

  it('accepts a platform admin with a shop', () => {
    expect(isUsableIdentity({ shop_id: 'shop-1', is_platform_admin: true })).toBe(true);
  });

  it('rejects a shop-less non-admin', () => {
    expect(isUsableIdentity({ shop_id: null })).toBe(false);
    expect(isUsableIdentity({ shop_id: null, is_platform_admin: false })).toBe(false);
  });

  it('rejects the pending_sync placeholder for everyone', () => {
    expect(isUsableIdentity({ shop_id: 'pending_sync' })).toBe(false);
    expect(isUsableIdentity({ shop_id: 'pending_sync', is_platform_admin: true })).toBe(false);
  });
});
