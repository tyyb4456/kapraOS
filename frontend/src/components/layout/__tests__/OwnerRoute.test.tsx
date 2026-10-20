import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { OwnerRoute } from '../OwnerRoute.tsx';

const authState = vi.hoisted(() => ({
  isAuthenticated: true,
  isLoading: false,
  role: 'owner' as string | null,
}));

vi.mock('../../../context/AuthContext.tsx', () => ({
  useAuth: () => ({ ...authState }),
}));

function renderOwnerRoute() {
  return render(
    <MemoryRouter initialEntries={['/employees']}>
      <Routes>
        <Route
          path="/employees"
          element={
            <OwnerRoute>
              <div>owner-surface</div>
            </OwnerRoute>
          }
        />
        <Route path="/sales/new" element={<div>pos-surface</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('OwnerRoute', () => {
  beforeEach(() => {
    authState.isAuthenticated = true;
    authState.isLoading = false;
    authState.role = 'owner';
  });

  it('renders the owner surface for an owner', () => {
    renderOwnerRoute();
    expect(screen.getByText('owner-surface')).toBeTruthy();
  });

  it.each(['cashier', 'staff'])('redirects %s to the POS', (role) => {
    authState.role = role;
    renderOwnerRoute();
    expect(screen.getByText('pos-surface')).toBeTruthy();
    expect(screen.queryByText('owner-surface')).toBeNull();
  });

  it('redirects unauthenticated users to the POS', () => {
    authState.isAuthenticated = false;
    renderOwnerRoute();
    expect(screen.getByText('pos-surface')).toBeTruthy();
  });

  it('shows a loading state while the identity loads', () => {
    authState.isLoading = true;
    renderOwnerRoute();
    expect(screen.getByText('Verifying shop permissions...')).toBeTruthy();
    expect(screen.queryByText('owner-surface')).toBeNull();
  });
});
