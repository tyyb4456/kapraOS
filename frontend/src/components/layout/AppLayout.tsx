import { useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar.tsx';
import { TopHeader } from './TopHeader.tsx';

const routeTitleMap: Record<string, string> = {
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
  '/settings': 'Shop Settings',
  '/admin': 'Platform Overview',
  '/admin/users': 'Platform Users',
  '/admin/shops': 'Platform Shops',
};

/**
 * Exact map hit, else the longest known prefix match (so detail routes like
 * /admin/users/:id or /customers/khata/:id inherit their parent's title).
 */
function resolvePageTitle(pathname: string): string {
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

export function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  const pageTitle = resolvePageTitle(location.pathname);

  return (
    <div className="flex min-h-screen bg-[var(--clay-bg)] text-zinc-900 dark:text-zinc-100">
      {/* Sidebar */}
      <Sidebar
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((prev) => !prev)}
        mobileOpen={mobileOpen}
        onCloseMobile={() => setMobileOpen(false)}
      />

      {/* Main Column */}
      <div className="flex flex-col flex-1 min-w-0">
        <TopHeader
          onOpenMobileMenu={() => setMobileOpen(true)}
          pageTitle={pageTitle}
        />

        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
