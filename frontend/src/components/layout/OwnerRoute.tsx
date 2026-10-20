import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.tsx';
import { isOwnerRole } from '../../context/identity.ts';
import { LoadingState } from './LoadingState.tsx';

/**
 * Gate for owner-only shop routes.
 *
 * Cashiers/salesmen are redirected to the POS. This only mirrors the
 * server-side `require_owner` dependency: every admin endpoint rejects
 * non-owners regardless of which page the browser renders.
 */
export function OwnerRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, role } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-zinc-50">
        <LoadingState message="Verifying shop permissions..." />
      </div>
    );
  }

  if (!isAuthenticated || !isOwnerRole(role)) {
    return <Navigate to="/sales/new" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
