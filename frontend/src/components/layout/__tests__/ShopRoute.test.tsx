import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ShopRoute } from '../ShopRoute.tsx';

const authState = vi.hoisted(() => ({
  isAuthenticated: true,
  isLoading: false,
  isPlatformAdmin: false,
}));

vi.mock('../../../context/AuthContext.tsx', () => ({
  useAuth: () => ({ ...authState }),
}));

function renderShopRoute() {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <Routes>
        <Route
          path="/dashboard"
          element={
            <ShopRoute>
              <div>shop-surface</div>
            </ShopRoute>
          }
        />
        <Route path="/admin" element={<div>platform-surface</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ShopRoute', () => {
  beforeEach(() => {
    authState.isAuthenticated = true;
    authState.isLoading = false;
    authState.isPlatformAdmin = false;
  });

  it('renders the shop surface for a regular user', () => {
    renderShopRoute();
    expect(screen.getByText('shop-surface')).toBeTruthy();
    expect(screen.queryByText('platform-surface')).toBeNull();
  });

  it('redirects platform admins to /admin even when they have a shop', () => {
    authState.isPlatformAdmin = true;
    renderShopRoute();
    expect(screen.getByText('platform-surface')).toBeTruthy();
    expect(screen.queryByText('shop-surface')).toBeNull();
  });

  it('shows a loading state while the identity loads', () => {
    authState.isLoading = true;
    renderShopRoute();
    expect(screen.getByText('Verifying workspace access...')).toBeTruthy();
  });
});
