import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.tsx';
import { LoadingState } from './LoadingState.tsx';

/**
 * Gate for every shop-facing route.
 *
 * Platform administrators never see the shop UI: once signed in they are
 * redirected to /admin, whether or not a shop happens to be attached to
 * their user row. Regular shop users pass through untouched.
 */
export function ShopRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, isPlatformAdmin } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-zinc-50">
        <LoadingState message="Verifying workspace access..." />
      </div>
    );
  }

  if (isAuthenticated && isPlatformAdmin) {
    return <Navigate to="/admin" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
