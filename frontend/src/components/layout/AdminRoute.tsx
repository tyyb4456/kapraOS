import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.tsx';
import { LoadingState } from './LoadingState.tsx';

/**
 * Gate for platform-admin-only routes.
 *
 * Access is decided by the server-side `is_platform_admin` flag carried on
 * the backend identity; this component only mirrors it for navigation. It
 * is not a security boundary by itself - every /admin API call is also
 * enforced by `require_platform_admin` on the backend.
 */
export function AdminRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, isPlatformAdmin } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-zinc-50">
        <LoadingState message="Verifying platform admin access..." />
      </div>
    );
  }

  if (!isAuthenticated || !isPlatformAdmin) {
    return <Navigate to="/dashboard" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
