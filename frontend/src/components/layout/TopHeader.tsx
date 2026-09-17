import { Menu } from 'lucide-react';
import { UserButton } from '@clerk/clerk-react';
import { useAuth } from '../../context/AuthContext.tsx';
import { Badge } from '../ui/Badge.tsx';

interface TopHeaderProps {
  onOpenMobileMenu: () => void;
  pageTitle?: string;
}

export function TopHeader({ onOpenMobileMenu, pageTitle = 'KapraOS' }: TopHeaderProps) {
  const { user, isClerkConfigured, signOut } = useAuth();

  return (
    <header className="h-14 bg-white border-b border-zinc-200 px-4 sm:px-6 flex items-center justify-between sticky top-0 z-20">
      {/* Left Area: Mobile Hamburger + Breadcrumb / Page Title */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onOpenMobileMenu}
          className="lg:hidden p-1.5 -ml-1 text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 rounded-md transition-colors cursor-pointer"
          aria-label="Open navigation menu"
        >
          <Menu className="w-5 h-5" />
        </button>

        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-600 hidden sm:inline">KapraOS</span>
          <span className="text-xs text-zinc-300 hidden sm:inline">/</span>
          <span className="text-sm font-semibold text-zinc-900 tracking-tight">
            {pageTitle}
          </span>
        </div>
      </div>

      {/* Right Area: Status & Clerk Account Control */}
      <div className="flex items-center gap-3">
        {user?.shopId && (
          <div className="hidden sm:flex items-center gap-1.5 bg-zinc-50 border border-zinc-200 px-2.5 py-1 rounded-full text-[11px] text-zinc-600">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="font-mono text-zinc-700">Shop: {user.shopId.slice(0, 8)}</span>
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
              className="text-xs text-zinc-500 hover:text-zinc-900 transition-colors font-medium px-2 py-1 rounded hover:bg-zinc-100 cursor-pointer"
            >
              Sign out
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
