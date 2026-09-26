import { Suspense, lazy } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { AppLayout } from '../components/layout/AppLayout.tsx';
import { ProtectedRoute } from '../components/layout/ProtectedRoute.tsx';

import { DashboardPage } from '../pages/DashboardPage.tsx';
import { ProductsPage } from '../pages/catalog/ProductsPage.tsx';
import { CategoriesPage } from '../pages/catalog/CategoriesPage.tsx';
import { InventoryPage } from '../pages/inventory/InventoryPage.tsx';
import { StockMovementsPage } from '../pages/inventory/StockMovementsPage.tsx';
import { SalesPage } from '../pages/sales/SalesPage.tsx';
import { NewSalePage } from '../pages/sales/NewSalePage.tsx';
import { PurchasesPage } from '../pages/purchasing/PurchasesPage.tsx';
import { NewPurchasePage } from '../pages/purchasing/NewPurchasePage.tsx';
import { CustomersPage } from '../pages/relationships/CustomersPage.tsx';
import { CustomerKhataPage } from '../pages/relationships/CustomerKhataPage.tsx';
import { SuppliersPage } from '../pages/relationships/SuppliersPage.tsx';
import { SupplierKhataPage } from '../pages/relationships/SupplierKhataPage.tsx';
import { ExpensesPage } from '../pages/finance/ExpensesPage.tsx';
import { ReportsPage } from '../pages/finance/ReportsPage.tsx';

// Heavy charting lib lives only here - lazy-load so the initial bundle stays lean.
const AnalyticsPage = lazy(() =>
  import('../pages/analytics/AnalyticsPage.tsx').then((m) => ({ default: m.AnalyticsPage })),
);
import { SettingsPage } from '../pages/settings/SettingsPage.tsx';
import { AiChatPage } from '../pages/ai/AiChatPage.tsx';
import { LoginPage } from '../pages/auth/LoginPage.tsx';
import { SignUpPage } from '../pages/auth/SignUpPage.tsx';
import { LandingPage } from '../pages/LandingPage.tsx';
import { NotFoundPage } from '../pages/NotFoundPage.tsx';

export function AppRouter() {
  return (
    <Routes>
      {/* Public marketing landing — entry point of the app */}
      <Route path="/" element={<LandingPage />} />

      {/* Public / Auth Routes (Wildcards required for Clerk SSO callbacks & sub-flows) */}
      <Route path="/login/*" element={<LoginPage />} />
      <Route path="/signup/*" element={<SignUpPage />} />
      <Route path="/sign-in/*" element={<Navigate to="/login" replace />} />
      <Route path="/sign-up/*" element={<Navigate to="/signup" replace />} />

      {/* Protected Application Routes inside AppLayout */}
      <Route
        element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/dashboard" element={<DashboardPage />} />

        {/* Catalog */}
        <Route path="/products" element={<ProductsPage />} />
        <Route path="/categories" element={<CategoriesPage />} />

        {/* Inventory */}
        <Route path="/inventory" element={<InventoryPage />} />
        <Route path="/inventory/movements" element={<StockMovementsPage />} />

        {/* Sales & POS */}
        <Route path="/sales" element={<SalesPage />} />
        <Route path="/sales/new" element={<NewSalePage />} />

        {/* Purchasing */}
        <Route path="/purchases" element={<PurchasesPage />} />
        <Route path="/purchases/new" element={<NewPurchasePage />} />

        {/* Relationships & Khata */}
        <Route path="/customers" element={<CustomersPage />} />
        <Route path="/customers/khata" element={<CustomerKhataPage />} />
        <Route path="/customers/khata/:id" element={<CustomerKhataPage />} />
        <Route path="/suppliers" element={<SuppliersPage />} />
        <Route path="/suppliers/khata" element={<SupplierKhataPage />} />
        <Route path="/suppliers/khata/:id" element={<SupplierKhataPage />} />

        {/* Finance */}
        <Route path="/expenses" element={<ExpensesPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route
          path="/analytics"
          element={
            <Suspense fallback={<div className="p-6 text-xs text-zinc-500">Loading analytics…</div>}>
              <AnalyticsPage />
            </Suspense>
          }
        />

        {/* Settings */}
        <Route path="/settings" element={<SettingsPage />} />

        {/* AI Assistant (live shop-assistant chat) */}
        <Route path="/ai-chat" element={<AiChatPage />} />
      </Route>

      {/* 404 Catch-all */}
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
