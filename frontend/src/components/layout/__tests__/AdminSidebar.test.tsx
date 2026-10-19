import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AdminSidebar } from '../AdminSidebar.tsx';

const authState = vi.hoisted(() => ({
  user: { fullName: 'Ada Admin', shopId: null as string | null },
  signOut: vi.fn(),
}));

vi.mock('../../../context/AuthContext.tsx', () => ({
  useAuth: () => ({ ...authState }),
}));

function renderAdminSidebar() {
  return render(
    <MemoryRouter>
      <AdminSidebar />
    </MemoryRouter>,
  );
}

describe('AdminSidebar', () => {
  it('renders only platform navigation', () => {
    const { container } = renderAdminSidebar();

    for (const [label, href] of [
      ['Overview', '/admin'],
      ['Platform Health', '/admin/health'],
      ['All Users', '/admin/users'],
      ['All Shops', '/admin/shops'],
    ]) {
      const link = screen.getByText(label).closest('a');
      expect(link?.getAttribute('href')).toBe(href);
    }

    expect(screen.getByText('Platform Admin')).toBeTruthy();
    expect(container.querySelector('button[title="Sign out"]')).toBeTruthy();
  });

  it('never renders shop navigation', () => {
    renderAdminSidebar();

    for (const label of [
      'Dashboard',
      'Products',
      'Categories',
      'Inventory',
      'New Sale',
      'Purchases',
      'Customers',
      'Expenses',
      'Settings',
      'AI Assistant',
    ]) {
      expect(screen.queryByText(label)).toBeNull();
    }
  });

  it('labels a shop-less admin as having no shop', () => {
    renderAdminSidebar();
    expect(screen.getByText('No shop')).toBeTruthy();
  });
});
