import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  Package,
  FolderTree,
  Warehouse,
  ArrowLeftRight,
  PlusCircle,
  Receipt,
  ShoppingCart,
  ShoppingBag,
  Users,
  BookOpen,
  Truck,
  Building2,
  Wallet,
  ChartNoAxesCombined,
  TrendingUp,
  Settings,
  ChevronLeft,
  ChevronRight,
  LogOut,
  Store,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext.tsx';
import { Badge } from '../ui/Badge.tsx';

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  mobileOpen?: boolean;
  onCloseMobile?: () => void;
}

interface NavItem {
  label: string;
  path: string;
  icon: React.ComponentType<{ className?: string }>;
}

interface NavGroup {
  groupTitle?: string;
  items: NavItem[];
}

const navigationGroups: NavGroup[] = [
  {
    items: [
      { label: 'Dashboard', path: '/dashboard', icon: LayoutDashboard },
    ],
  },
  {
    groupTitle: 'CATALOG',
    items: [
      { label: 'Products', path: '/products', icon: Package },
      { label: 'Categories', path: '/categories', icon: FolderTree },
    ],
  },
  {
    groupTitle: 'INVENTORY',
    items: [
      { label: 'Inventory', path: '/inventory', icon: Warehouse },
      { label: 'Stock Movements', path: '/inventory/movements', icon: ArrowLeftRight },
    ],
  },
  {
    groupTitle: 'SALES',
    items: [
      { label: 'New Sale', path: '/sales/new', icon: PlusCircle },
      { label: 'Sales', path: '/sales', icon: Receipt },
    ],
  },
  {
    groupTitle: 'PURCHASING',
    items: [
      { label: 'New Purchase', path: '/purchases/new', icon: ShoppingCart },
      { label: 'Purchases', path: '/purchases', icon: ShoppingBag },
    ],
  },
  {
    groupTitle: 'RELATIONSHIPS',
    items: [
      { label: 'Customers', path: '/customers', icon: Users },
      { label: 'Customer Khata', path: '/customers/khata', icon: BookOpen },
      { label: 'Suppliers', path: '/suppliers', icon: Truck },
      { label: 'Supplier Khata', path: '/suppliers/khata', icon: Building2 },
    ],
  },
  {
    groupTitle: 'FINANCE',
    items: [
      { label: 'Expenses', path: '/expenses', icon: Wallet },
      { label: 'Reports', path: '/reports', icon: ChartNoAxesCombined },
      { label: 'Analytics', path: '/analytics', icon: TrendingUp },
    ],
  },
  {
    items: [
      { label: 'Settings', path: '/settings', icon: Settings },
    ],
  },
];

export function Sidebar({
  collapsed,
  onToggleCollapse,
  mobileOpen = false,
  onCloseMobile,
}: SidebarProps) {
  const { user, signOut, role } = useAuth();

  const sidebarContent = (
    <div className="flex flex-col h-full bg-white dark:bg-zinc-950 border-r border-zinc-200 dark:border-zinc-800 select-none">
      {/* Brand Header */}
      <div className="flex items-center justify-between h-14 px-4 border-b border-zinc-100 dark:border-zinc-800 shrink-0">
        <div className="flex items-center gap-2.5 overflow-hidden">
          <div className="w-8 h-8 rounded-md bg-zinc-900 dark:bg-zinc-100 flex items-center justify-center text-white dark:text-zinc-900 shrink-0 shadow-xs">
            <Store className="w-4 h-4" />
          </div>
          {!collapsed && (
            <div className="flex flex-col min-w-0">
              <span className="text-sm font-bold tracking-tight text-zinc-900 dark:text-zinc-50 truncate">
                KapraOS
              </span>
              <span className="text-[10px] font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wider truncate">
                Fabric Retail OS
              </span>
            </div>
          )}
        </div>

        {/* Desktop Collapse Toggle */}
        <button
          type="button"
          onClick={onToggleCollapse}
          className="hidden lg:flex items-center justify-center w-6 h-6 rounded text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
        </button>
      </div>

      {/* Navigation Links (Scrollable) */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-4">
        {navigationGroups.map((group, groupIdx) => (
          <div key={groupIdx} className="space-y-0.5">
            {group.groupTitle && !collapsed && (
              <h4 className="px-2.5 pt-1.5 pb-1 text-[10px] font-semibold tracking-wider text-zinc-500 dark:text-zinc-400 uppercase">
                {group.groupTitle}
              </h4>
            )}
            {group.items.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.path}
                  to={item.path}
                  onClick={onCloseMobile}
                  className={({ isActive }) =>
                    `flex items-center gap-2.5 px-2.5 py-1.5 text-xs rounded-md transition-colors ${
                      isActive
                        ? 'bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900 font-medium shadow-xs'
                        : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100/80 dark:hover:bg-zinc-800/70 font-normal'
                    } ${collapsed ? 'justify-center px-2' : ''}`
                  }
                  title={collapsed ? item.label : undefined}
                >
                  <Icon className="w-4 h-4 shrink-0" />
                  {!collapsed && <span className="truncate">{item.label}</span>}
                </NavLink>
              );
            })}
          </div>
        ))}
      </div>

      {/* User & Shop Bottom Widget */}
      <div className="p-3 border-t border-zinc-100 dark:border-zinc-800 shrink-0 bg-zinc-50/60 dark:bg-zinc-900/50">
        <div className={`flex items-center ${collapsed ? 'justify-center' : 'justify-between gap-2'}`}>
          {!collapsed ? (
            <div className="flex items-center gap-2 min-w-0 flex-1">
              <div className="w-7 h-7 rounded-full bg-zinc-200 dark:bg-zinc-700 text-zinc-700 dark:text-zinc-200 font-semibold text-xs flex items-center justify-center shrink-0">
                {user?.fullName?.charAt(0).toUpperCase() || 'U'}
              </div>
              <div className="flex flex-col min-w-0 flex-1">
                <span className="text-xs font-medium text-zinc-900 dark:text-zinc-100 truncate">
                  {user?.fullName || 'Shopkeeper'}
                </span>
                <div className="flex items-center gap-1.5">
                  <Badge variant="neutral" size="sm" className="text-[9px] py-0 px-1.5">
                    {role || 'staff'}
                  </Badge>
                  <span className="text-[10px] text-zinc-500 dark:text-zinc-400 truncate">
                    {user?.shopId ? `Shop #${user.shopId.slice(0, 6)}` : 'Isolated'}
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <div
              className="w-7 h-7 rounded-full bg-zinc-200 dark:bg-zinc-700 text-zinc-700 dark:text-zinc-200 font-semibold text-xs flex items-center justify-center shrink-0"
              title={user?.fullName || 'User'}
            >
              {user?.fullName?.charAt(0).toUpperCase() || 'U'}
            </div>
          )}

          {!collapsed && (
            <button
              type="button"
              onClick={() => signOut()}
              className="p-1 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-200/60 dark:hover:bg-zinc-800 rounded transition-colors cursor-pointer"
              title="Sign out"
            >
              <LogOut className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop Sidebar */}
      <aside
        className={`hidden lg:block shrink-0 transition-all duration-200 h-screen sticky top-0 z-30 ${
          collapsed ? 'w-16' : 'w-60'
        }`}
      >
        {sidebarContent}
      </aside>

      {/* Mobile Drawer Backdrop */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="fixed inset-0 bg-zinc-950/40 backdrop-blur-xs transition-opacity"
            onClick={onCloseMobile}
            aria-hidden="true"
          />
          <div className="fixed inset-y-0 left-0 w-64 z-50 shadow-xl">
            {sidebarContent}
          </div>
        </div>
      )}
    </>
  );
}
