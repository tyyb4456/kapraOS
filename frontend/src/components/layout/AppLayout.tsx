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
  '/settings': 'Shop Settings',
};

export function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  const pageTitle = routeTitleMap[location.pathname] || 'KapraOS';

  return (
    <div className="flex min-h-screen bg-zinc-50 text-zinc-900">
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
