import { describe, expect, it } from 'vitest';
import {
  adminRouteTitleMap,
  resolvePageTitle,
  shopRouteTitleMap,
} from '../pageTitles.ts';

describe('resolvePageTitle', () => {
  it('resolves exact routes in both layouts', () => {
    expect(resolvePageTitle('/dashboard', shopRouteTitleMap)).toBe('Dashboard');
    expect(resolvePageTitle('/settings', shopRouteTitleMap)).toBe('Shop Settings');
    expect(resolvePageTitle('/admin', adminRouteTitleMap)).toBe('Platform Overview');
    expect(resolvePageTitle('/admin/health', adminRouteTitleMap)).toBe('Platform Health');
  });

  it('inherits the longest matching prefix for detail routes', () => {
    expect(resolvePageTitle('/admin/users/user-1', adminRouteTitleMap)).toBe('Platform Users');
    expect(resolvePageTitle('/admin/shops/shop-1', adminRouteTitleMap)).toBe('Platform Shops');
    expect(resolvePageTitle('/customers/khata/cus-1', shopRouteTitleMap)).toBe(
      'Customer Khata (Receivables)',
    );
  });

  it('falls back to the brand for unknown paths', () => {
    expect(resolvePageTitle('/nope', shopRouteTitleMap)).toBe('KapraOS');
    expect(resolvePageTitle('/nope', adminRouteTitleMap)).toBe('KapraOS');
  });

  it('keeps the platform and shop title maps disjoint', () => {
    const shopPaths = Object.keys(shopRouteTitleMap);
    const adminPaths = Object.keys(adminRouteTitleMap);

    expect(shopPaths.some((path) => path.startsWith('/admin'))).toBe(false);
    expect(adminPaths.some((path) => path.startsWith('/admin'))).toBe(true);
    expect(adminPaths.some((path) => shopPaths.includes(path))).toBe(false);
  });
});
