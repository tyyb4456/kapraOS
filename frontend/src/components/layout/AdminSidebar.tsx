import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  Shield,
  Activity,
  Users,
  Building2,
  LogOut,
  Store,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext.tsx';
import { Badge } from '../ui/Badge.tsx';

interface AdminSidebarProps {
  mobileOpen?: boolean;
  onCloseMobile?: () => void;
}

interface NavItem {
  label: string;
  path: string;
  icon: React.ComponentType<{ className?: string }>;
  end?: boolean;
}

// The platform administration surface is deliberately small: no catalog,
// no POS, no shop routes. An admin only ever uses these four screens.
const adminNavItems: NavItem[] = [
  { label: 'Overview', path: '/admin', icon: Shield, end: true },
  { label: 'Platform Health', path: '/admin/health', icon: Activity },
  { label: 'All Users', path: '/admin/users', icon: Users },
  { label: 'All Shops', path: '/admin/shops', icon: Building2 },
];

export function AdminSidebar({ mobileOpen = false, onCloseMobile }: AdminSidebarProps) {
  const { user, signOut } = useAuth();

  const sidebarContent = (
    <div className="clay-sidebar flex flex-col h-full select-none">
      {/* Brand Header */}
      <div className="flex items-center h-14 px-4 border-b border-[var(--clay-border-soft)] shrink-0">
        <div className="flex items-center gap-2.5 overflow-hidden">
          <div className="clay-icon-tile w-8 h-8 bg-[var(--clay-primary)] text-white border-transparent rounded-[14px]">
            <Store className="w-4 h-4" />
          </div>
          <div className="flex flex-col min-w-0">
            <span className="text-sm font-bold tracking-tight text-zinc-900 dark:text-zinc-50 truncate">
              KapraOS
            </span>
            <span className="text-[10px] font-medium text-[var(--clay-primary-active)] uppercase tracking-wider truncate">
              Platform Admin
            </span>
          </div>
        </div>
      </div>

      {/* Platform-only Navigation */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-4">
        <div className="space-y-0.5">
          <h4 className="px-2.5 pt-1.5 pb-1 text-[10px] font-semibold tracking-wider text-zinc-500 dark:text-zinc-400 uppercase">
            PLATFORM
          </h4>
          {adminNavItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.end}
                onClick={onCloseMobile}
                className={({ isActive }) =>
                  `flex items-center gap-2.5 px-2.5 py-1.5 text-xs rounded-xl transition-all ${
                    isActive
                      ? 'clay-nav-active font-medium'
                      : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100/80 dark:hover:bg-zinc-800/70 font-normal border border-transparent'
                  }`
                }
              >
                <Icon className="w-4 h-4 shrink-0" />
                <span className="truncate">{item.label}</span>
              </NavLink>
            );
          })}
        </div>
      </div>

      {/* Admin Identity & Sign Out */}
      <div className="p-3 border-t border-[var(--clay-border-soft)] shrink-0 bg-[var(--clay-surface-soft)]">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <div className="clay-chip-accent w-7 h-7 rounded-full bg-[var(--clay-primary-soft)] text-[var(--clay-primary-active)] dark:text-[#c7c7fb] font-semibold text-xs flex items-center justify-center shrink-0">
              {user?.fullName?.charAt(0).toUpperCase() || 'A'}
            </div>
            <div className="flex flex-col min-w-0 flex-1">
              <span className="text-xs font-medium text-zinc-900 dark:text-zinc-100 truncate">
                {user?.fullName || 'Platform Admin'}
              </span>
              <div className="flex items-center gap-1.5">
                <Badge variant="warning" size="sm" className="text-[9px] py-0 px-1.5">
                  <Shield className="w-2.5 h-2.5" />
                  Admin
                </Badge>
                <span className="text-[10px] text-zinc-500 dark:text-zinc-400 truncate">
                  {user?.shopId ? `Shop #${user.shopId.slice(0, 6)}` : 'No shop'}
                </span>
              </div>
            </div>
          </div>

          <button
            type="button"
            onClick={() => signOut()}
            className="clay-icon-tile p-1.5 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 transition-colors cursor-pointer"
            title="Sign out"
          >
            <LogOut className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop Sidebar */}
      <aside className="hidden lg:block shrink-0 h-screen sticky top-0 z-30 w-60">
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
