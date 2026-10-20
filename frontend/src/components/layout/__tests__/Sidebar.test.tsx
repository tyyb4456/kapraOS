import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Sidebar } from '../Sidebar.tsx';

const authState = vi.hoisted(() => ({
  user: { fullName: 'Owner Admin', shopId: 'shop-123456' as string | null },
  role: 'owner' as string | null,
  signOut: vi.fn(),
}));

vi.mock('../../../context/AuthContext.tsx', () => ({
  useAuth: () => ({ ...authState }),
}));

function renderSidebar() {
  return render(
    <MemoryRouter>
      <Sidebar collapsed={false} onToggleCollapse={() => {}} />
    </MemoryRouter>,
  );
}

describe('Sidebar (shop layout)', () => {
  beforeEach(() => {
    authState.role = 'owner';
  });

  it('renders the shop navigation', () => {
    renderSidebar();
    expect(screen.getByText('Dashboard')).toBeTruthy();
    expect(screen.getByText('Products')).toBeTruthy();
    expect(screen.getByText('Employees')).toBeTruthy();
    expect(screen.getByText('Settings')).toBeTruthy();
  });

  it('shows only POS and customer links to a cashier', () => {
    authState.role = 'cashier';
    renderSidebar();
    expect(screen.getByText('New Sale')).toBeTruthy();
    expect(screen.getByText('Sales')).toBeTruthy();
    expect(screen.getByText('Customers')).toBeTruthy();
    expect(screen.queryByText('Dashboard')).toBeNull();
    expect(screen.queryByText('Products')).toBeNull();
    expect(screen.queryByText('Suppliers')).toBeNull();
    expect(screen.queryByText('Reports')).toBeNull();
    expect(screen.queryByText('Employees')).toBeNull();
    expect(screen.queryByText('Settings')).toBeNull();
  });

  it('does not render a platform admin group for platform admins', () => {
    const { container } = renderSidebar();

    expect(screen.queryByText('PLATFORM ADMIN')).toBeNull();
    expect(screen.queryByText('Platform Overview')).toBeNull();
    expect(screen.queryByText('All Users')).toBeNull();
    expect(screen.queryByText('All Shops')).toBeNull();
    expect(container.querySelector('a[href="/admin"]')).toBeNull();
  });
});
