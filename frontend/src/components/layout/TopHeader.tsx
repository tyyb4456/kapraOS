import { Menu } from 'lucide-react';
import { UserButton } from '@clerk/clerk-react';
import { useAuth } from '../../context/AuthContext.tsx';
import { Badge } from '../ui/Badge.tsx';
import { ThemeToggle } from '../ui/ThemeToggle.tsx';

interface TopHeaderProps {
  onOpenMobileMenu: () => void;
  pageTitle?: string;
}

export function TopHeader({ onOpenMobileMenu, pageTitle = 'KapraOS' }: TopHeaderProps) {
  const { user, isClerkConfigured, signOut } = useAuth();

  return (
    <header className="h-14 bg-white dark:bg-zinc-950 border-b border-zinc-200 dark:border-zinc-800 px-4 sm:px-6 flex items-center justify-between sticky top-0 z-20">
      {/* Left Area: Mobile Hamburger + Breadcrumb / Page Title */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onOpenMobileMenu}
          className="lg:hidden p-1.5 -ml-1 text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded-md transition-colors cursor-pointer"
          aria-label="Open navigation menu"
        >
          <Menu className="w-5 h-5" />
        </button>

        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-600 dark:text-zinc-400 hidden sm:inline">KapraOS</span>
          <span className="text-xs text-zinc-300 dark:text-zinc-700 hidden sm:inline">/</span>
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 tracking-tight">
            {pageTitle}
          </span>
        </div>
      </div>

      {/* Right Area: Theme, Status & Clerk Account Control */}
      <div className="flex items-center gap-2 sm:gap-3">
        <ThemeToggle />
        <div className="w-px h-5 bg-zinc-200 dark:bg-zinc-800" aria-hidden="true" />

        {user?.shopId && (
          <div className="hidden sm:flex items-center gap-1.5 bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 px-2.5 py-1 rounded-full text-[11px] text-zinc-600 dark:text-zinc-400">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="font-mono text-zinc-700 dark:text-zinc-300">Shop: {user.shopId.slice(0, 8)}</span>
          </div>
        )}

        {isClerkConfigured ? (
          <div className="flex items-center pl-1">
            <UserButton
              afterSignOutUrl="/login"
              appearance={{
                elements: {
                  avatarBox: 'w-7 h-7',
                },
              }}
            />
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <Badge variant="neutral" size="sm" className="hidden md:inline-flex text-[10px]">
              Dev Mode
            </Badge>
            <button
              type="button"
              onClick={() => signOut()}
              className="text-xs text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors font-medium px-2 py-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer"
            >
              Sign out
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
