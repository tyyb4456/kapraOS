import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AdminRoute } from '../AdminRoute.tsx';

const authState = vi.hoisted(() => ({
  isAuthenticated: true,
  isLoading: false,
  isPlatformAdmin: true,
}));

vi.mock('../../../context/AuthContext.tsx', () => ({
  useAuth: () => ({ ...authState }),
}));

function renderAdminRoute() {
  return render(
    <MemoryRouter initialEntries={['/admin']}>
      <Routes>
        <Route
          path="/admin"
          element={
            <AdminRoute>
              <div>admin-surface</div>
            </AdminRoute>
          }
        />
        <Route path="/dashboard" element={<div>shop-dashboard</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('AdminRoute', () => {
  beforeEach(() => {
    authState.isAuthenticated = true;
    authState.isLoading = false;
    authState.isPlatformAdmin = true;
  });

  it('renders the platform surface for a platform admin', () => {
    renderAdminRoute();
    expect(screen.getByText('admin-surface')).toBeTruthy();
  });

  it('renders for a platform admin without a shop', () => {
    // AdminRoute only reads the flags; a null shopId must not matter.
    renderAdminRoute();
    expect(screen.queryByText('shop-dashboard')).toBeNull();
    expect(screen.getByText('admin-surface')).toBeTruthy();
  });

  it('redirects non-admins to the shop dashboard', () => {
    authState.isPlatformAdmin = false;
    renderAdminRoute();
    expect(screen.getByText('shop-dashboard')).toBeTruthy();
    expect(screen.queryByText('admin-surface')).toBeNull();
  });

  it('shows a loading state while the identity loads', () => {
    authState.isLoading = true;
    renderAdminRoute();
    expect(screen.getByText('Verifying platform admin access...')).toBeTruthy();
  });
});
