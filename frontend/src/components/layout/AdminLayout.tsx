import { useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { AdminSidebar } from './AdminSidebar.tsx';
import { TopHeader } from './TopHeader.tsx';
import { adminRouteTitleMap, resolvePageTitle } from './pageTitles.ts';

/**
 * Shell for the platform-only interface. A platform administrator only
 * ever sees this layout: no shop navigation, no POS, no catalog.
 */
export function AdminLayout() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  const pageTitle = resolvePageTitle(location.pathname, adminRouteTitleMap);

  return (
    <div className="flex min-h-screen bg-[var(--clay-bg)] text-zinc-900 dark:text-zinc-100">
      {/* Platform Sidebar */}
      <AdminSidebar
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
