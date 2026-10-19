import { useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar.tsx';
import { TopHeader } from './TopHeader.tsx';
import { resolvePageTitle, shopRouteTitleMap } from './pageTitles.ts';

export function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  const pageTitle = resolvePageTitle(location.pathname, shopRouteTitleMap);

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
