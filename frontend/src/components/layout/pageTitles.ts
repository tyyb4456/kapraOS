// Page titles per layout. Kept framework-free so both layouts (and tests)
// share the same prefix-matching behavior.

export const shopRouteTitleMap: Record<string, string> = {
  '/dashboard': 'Dashboard',
  '/products': 'Products Catalog',
  '/categories': 'Product Categories',
  '/inventory': 'Stock Inventory',
  '/inventory/movements': 'Stock Movements',
  '/sales': 'Sales History',
  '/sales/new': 'New Sale (POS)',
  '/purchases': 'Purchase Orders',
  '/purchases/new': 'New Purchase Order',
  '/customers': 'Customer Directory',
  '/customers/khata': 'Customer Khata (Receivables)',
  '/suppliers': 'Supplier Directory',
  '/suppliers/khata': 'Supplier Khata (Payables)',
  '/expenses': 'Shop Expenses',
  '/reports': 'Financial & Operational Reports',
  '/analytics': 'Sales Analytics',
  '/employees': 'Employees & Invitations',
  '/settings': 'Shop Settings',
};

export const adminRouteTitleMap: Record<string, string> = {
  '/admin': 'Platform Overview',
  '/admin/health': 'Platform Health',
  '/admin/users': 'Platform Users',
  '/admin/shops': 'Platform Shops',
};

/**
 * Exact map hit, else the longest known prefix match (so detail routes like
 * /admin/users/:id or /customers/khata/:id inherit their parent's title).
 */
export function resolvePageTitle(
  pathname: string,
  routeTitleMap: Record<string, string>,
): string {
  const exact = routeTitleMap[pathname];
  if (exact) return exact;

  let best = 'KapraOS';
  let bestLength = 0;
  for (const [prefix, title] of Object.entries(routeTitleMap)) {
    if (pathname.startsWith(`${prefix}/`) && prefix.length > bestLength) {
      best = title;
      bestLength = prefix.length;
    }
  }
  return best;
}
