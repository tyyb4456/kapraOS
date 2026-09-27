import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  TrendingUp,
  CreditCard,
  ShoppingBag,
  Wallet,
  Plus,
  Receipt,
  ShoppingCart,
  Users,
  Package,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext.tsx';
import {
  PageContainer,
  PageHeader,
  SectionHeader,
} from '../components/layout/index.ts';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card.tsx';
import { Button } from '../components/ui/Button.tsx';
import { Badge } from '../components/ui/Badge.tsx';
import { EmptyState } from '../components/ui/EmptyState.tsx';
import { Skeleton } from '../components/ui/Skeleton.tsx';
import { Alert } from '../components/ui/Alert.tsx';
import { formatCurrency, formatDate } from '../lib/formatters.ts';
import { getDashboard } from '../lib/api/dashboard.ts';
import { getSales } from '../lib/api/sales.ts';
import { getInventory } from '../lib/api/inventory.ts';
import type { DashboardSummary, Sale, InventoryItem } from '../types/index.ts';

export function DashboardPage() {
  const { user } = useAuth();
  const [dashboard, setDashboard] = useState<DashboardSummary | null>(null);
  const [recentSales, setRecentSales] = useState<Sale[]>([]);
  const [lowStock, setLowStock] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const getGreeting = () => {
    const hour = new Date().getHours();
    if (hour < 12) return 'Good morning';
    if (hour < 18) return 'Good afternoon';
    return 'Good evening';
  };

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const [dash, sales, low] = await Promise.all([
          getDashboard(),
          getSales({ limit: 5 }).catch(() => [] as Sale[]),
          getInventory({ low_stock: true }).catch(() => [] as InventoryItem[]),
        ]);
        if (cancelled) return;
        setDashboard(dash);
        setRecentSales(sales);
        setLowStock(low.slice(0, 5));
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load dashboard');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const metricValue = (value: number | undefined | null) =>
    loading ? <Skeleton className="h-7 w-28" /> : formatCurrency(Number(value ?? 0));

  const toNum = (v: unknown): number => {
    const n = Number(v ?? 0);
    return Number.isFinite(n) ? n : 0;
  };

  return (
    <PageContainer>
      {/* Top Welcome Header */}
      <PageHeader
        title={`${getGreeting()}, ${user?.fullName || 'Shop Owner'}`}
        description="Daily operational overview for your textile & retail store."
        actions={
          <div className="flex items-center gap-2">
            <Link to="/sales/new">
              <Button
                variant="primary"
                size="sm"
                leftIcon={<Receipt className="w-3.5 h-3.5" />}
              >
                New Sale (POS)
              </Button>
            </Link>
            <Link to="/purchases/new">
              <Button
                variant="outline"
                size="sm"
                leftIcon={<Plus className="w-3.5 h-3.5" />}
              >
                New Purchase
              </Button>
            </Link>
          </div>
        }
      />

      {error && (
        <Alert variant="danger" title="Could not load dashboard">
          {error}
        </Alert>
      )}

      {/* Today's Overview Metric Shells */}
      <div>
        <SectionHeader
          title="Today's Operational Pulse"
          subtitle="Real-time transaction highlights recorded today"
        />

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2 border-none">
              <CardTitle className="text-xs font-medium text-zinc-500">
                Today's Sales (Net)
              </CardTitle>
              <div className="w-7 h-7 rounded-md bg-emerald-50 text-emerald-600 flex items-center justify-center">
                <TrendingUp className="w-4 h-4" />
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              <div className="text-xl font-bold font-tabular text-zinc-900">
                {metricValue(
                  dashboard?.today_net_sales ?? dashboard?.today_sales,
                )}
              </div>
              <div className="flex flex-wrap items-center gap-1.5 mt-1">
                <Badge variant="neutral" size="sm">
                  {loading
                    ? '…'
                    : `${dashboard?.today_sales_count ?? 0} transaction${(dashboard?.today_sales_count ?? 0) === 1 ? '' : 's'}`}
                </Badge>
                {!loading && toNum(dashboard?.today_returns_total) > 0 ? (
                  <span className="text-[11px] text-zinc-500">
                    {formatCurrency(toNum(dashboard?.today_sales))} gross
                    <span className="text-rose-600"> −{formatCurrency(toNum(dashboard?.today_returns_total))} returns</span>
                    {toNum(dashboard?.today_returns_count) > 0 && (
                      <span> ({dashboard?.today_returns_count} return{(dashboard?.today_returns_count ?? 0) === 1 ? '' : 's'})</span>
                    )}
                  </span>
                ) : (
                  <span className="text-[11px] text-zinc-400">Recorded today</span>
                )}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2 border-none">
              <CardTitle className="text-xs font-medium text-zinc-500">
                Customer Payments (Net)
              </CardTitle>
              <div className="w-7 h-7 rounded-md bg-sky-50 text-sky-600 flex items-center justify-center">
                <CreditCard className="w-4 h-4" />
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              <div className="text-xl font-bold font-tabular text-zinc-900">
                {metricValue(dashboard?.today_net_payments_received ?? dashboard?.today_payments_received)}
              </div>
              <div className="flex flex-wrap items-center gap-1.5 mt-1">
                <Badge variant="neutral" size="sm">
                  {loading ? '…' : `${dashboard?.today_payment_count ?? 0} collection${(dashboard?.today_payment_count ?? 0) === 1 ? '' : 's'}`}
                </Badge>
                {!loading && (toNum(dashboard?.today_cash_refunds_total) > 0 || toNum(dashboard?.today_ar_reduction_total) > 0) ? (
                  <span className="text-[11px] text-zinc-500">
                    {formatCurrency(toNum(dashboard?.today_payments_received))} collected
                    {toNum(dashboard?.today_cash_refunds_total) > 0 && (
                      <span className="text-rose-600"> −{formatCurrency(toNum(dashboard?.today_cash_refunds_total))} cash back</span>
                    )}
                    {toNum(dashboard?.today_ar_reduction_total) > 0 && (
                      <span className="text-amber-600"> • −{formatCurrency(toNum(dashboard?.today_ar_reduction_total))} Khata</span>
                    )}
                    {toNum(dashboard?.today_returns_count) > 0 && (
                      <span> ({dashboard?.today_returns_count} return{(dashboard?.today_returns_count ?? 0) === 1 ? '' : 's'})</span>
                    )}
                  </span>
                ) : (
                  <span className="text-[11px] text-zinc-400">Cash & Khata</span>
                )}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2 border-none">
              <CardTitle className="text-xs font-medium text-zinc-500">
                Purchases Inward (Net)
              </CardTitle>
              <div className="w-7 h-7 rounded-md bg-zinc-100 text-zinc-700 flex items-center justify-center">
                <ShoppingBag className="w-4 h-4" />
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              <div className="text-xl font-bold font-tabular text-zinc-900">
                {metricValue(dashboard?.today_net_purchases ?? dashboard?.today_purchases)}
              </div>
              <div className="flex flex-wrap items-center gap-1.5 mt-1">
                <Badge variant="neutral" size="sm">
                  {loading
                    ? '…'
                    : `${dashboard?.today_purchase_count ?? 0} shipment${(dashboard?.today_purchase_count ?? 0) === 1 ? '' : 's'}`}
                </Badge>
                {!loading && toNum(dashboard?.today_purchase_returns_total) > 0 ? (
                  <span className="text-[11px] text-zinc-500">
                    {formatCurrency(toNum(dashboard?.today_purchases))} gross
                    <span className="text-rose-600"> −{formatCurrency(toNum(dashboard?.today_purchase_returns_total))} returns</span>
                    {toNum(dashboard?.today_purchase_returns_count) > 0 && (
                      <span> ({dashboard?.today_purchase_returns_count} return{(dashboard?.today_purchase_returns_count ?? 0) === 1 ? '' : 's'})</span>
                    )}
                  </span>
                ) : (
                  <span className="text-[11px] text-zinc-400">Inward inventory</span>
                )}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2 border-none">
              <CardTitle className="text-xs font-medium text-zinc-500">
                Shop Expenses
              </CardTitle>
              <div className="w-7 h-7 rounded-md bg-amber-50 text-amber-600 flex items-center justify-center">
                <Wallet className="w-4 h-4" />
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              <div className="text-xl font-bold font-tabular text-zinc-900">
                {metricValue(dashboard?.today_expenses)}
              </div>
              <div className="flex items-center gap-1.5 mt-1">
                <Badge variant="neutral" size="sm">
                  Daily petty cash
                </Badge>
                <span className="text-[11px] text-zinc-400">Utilities & bills</span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Quick Action Shortcuts */}
      <Card>
        <CardContent className="p-4 sm:p-5">
          <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-3">
            Quick Operations
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Link
              to="/sales/new"
              className="flex items-center gap-2.5 p-3 rounded-md border border-zinc-200 hover:border-zinc-300 hover:bg-zinc-50/80 transition-colors"
            >
              <Receipt className="w-4 h-4 text-zinc-700 shrink-0" />
              <div className="text-left">
                <div className="text-xs font-semibold text-zinc-900">Counter Sale</div>
                <div className="text-[10px] text-zinc-500">Instant POS checkout</div>
              </div>
            </Link>

            <Link
              to="/purchases/new"
              className="flex items-center gap-2.5 p-3 rounded-md border border-zinc-200 hover:border-zinc-300 hover:bg-zinc-50/80 transition-colors"
            >
              <ShoppingCart className="w-4 h-4 text-zinc-700 shrink-0" />
              <div className="text-left">
                <div className="text-xs font-semibold text-zinc-900">Add Purchase</div>
                <div className="text-[10px] text-zinc-500">Record cloth roll arrival</div>
              </div>
            </Link>

            <Link
              to="/products"
              className="flex items-center gap-2.5 p-3 rounded-md border border-zinc-200 hover:border-zinc-300 hover:bg-zinc-50/80 transition-colors"
            >
              <Package className="w-4 h-4 text-zinc-700 shrink-0" />
              <div className="text-left">
                <div className="text-xs font-semibold text-zinc-900">Fabric Catalog</div>
                <div className="text-[10px] text-zinc-500">Manage varieties & prices</div>
              </div>
            </Link>

            <Link
              to="/customers/khata"
              className="flex items-center gap-2.5 p-3 rounded-md border border-zinc-200 hover:border-zinc-300 hover:bg-zinc-50/80 transition-colors"
            >
              <Users className="w-4 h-4 text-zinc-700 shrink-0" />
              <div className="text-left">
                <div className="text-xs font-semibold text-zinc-900">Customer Khata</div>
                <div className="text-[10px] text-zinc-500">Receivables ledger</div>
              </div>
            </Link>
          </div>
        </CardContent>
      </Card>

      {/* Two Column Shell for Activity & Alerts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recent Activity Ledger Shell */}
        <div className="lg:col-span-2 space-y-3">
          <SectionHeader
            title="Recent Store Activity"
            subtitle="Latest sales and inventory movements"
            action={
              <Link to="/sales">
                <Button variant="ghost" size="sm" className="text-xs">
                  View all sales
                </Button>
              </Link>
            }
          />
          <Card>
            <CardContent className="p-6">
              {loading ? (
                <div className="space-y-3">
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                </div>
              ) : recentSales.length > 0 ? (
                <div className="divide-y divide-zinc-100">
                  {recentSales.map((sale) => {
                    const gross = toNum(sale.total_amount);
                    const returned = toNum((sale as { returned_total?: unknown }).returned_total);
                    const netRaw = (sale as { net_total?: unknown }).net_total;
                    const net =
                      netRaw === null || netRaw === undefined ? gross - returned : toNum(netRaw);
                    const hasReturn = returned > 0.005;
                    return (
                    <Link
                      key={sale.id}
                      to="/sales"
                      className="flex items-center justify-between py-3 first:pt-0 last:pb-0 hover:bg-zinc-50/60 rounded px-2 -mx-2 transition-colors"
                    >
                      <div className="min-w-0">
                        <div className="text-sm font-semibold text-zinc-900 truncate">
                          {sale.invoice_number || 'Counter sale'}
                          {sale.customer_name ? (
                            <span className="font-normal text-zinc-500"> · {sale.customer_name}</span>
                          ) : null}
                        </div>
                        <div className="text-[11px] text-zinc-400 mt-0.5">
                          {formatDate(sale.created_at, true)} · {sale.items_count} item{(sale.items_count ?? 0) === 1 ? '' : 's'} · {hasReturn && net <= 0.005 ? 'returned' : sale.status}
                          {hasReturn && (
                            <span className="text-rose-500"> · −{formatCurrency(returned)} returned</span>
                          )}
                        </div>
                      </div>
                      <div className="text-right shrink-0 ml-4">
                        <div className="text-sm font-bold font-tabular text-zinc-900">
                          {formatCurrency(net)}
                        </div>
                        {hasReturn && (
                          <div className="text-[11px] font-normal text-zinc-400 line-through">
                            {formatCurrency(gross)}
                          </div>
                        )}
                      </div>
                    </Link>
                    );
                  })}
                </div>
              ) : (
                <EmptyState
                  title="No transactions recorded today"
                  description="When sales or stock movements occur, they will appear chronologically in this feed."
                  action={
                    <Link to="/sales/new">
                      <Button variant="outline" size="sm">
                        Create First Sale
                      </Button>
                    </Link>
                  }
                />
              )}
            </CardContent>
          </Card>
        </div>

        {/* Low Stock Alerts Shell */}
        <div className="space-y-3">
          <SectionHeader
            title="Inventory Attention"
            subtitle="Items nearing reorder threshold"
            action={
              <Link to="/inventory">
                <Button variant="ghost" size="sm" className="text-xs">
                  Inventory
                </Button>
              </Link>
            }
          />
          <Card>
            <CardContent className="p-6">
              {loading ? (
                <div className="space-y-3">
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                </div>
              ) : lowStock.length > 0 ? (
                <div className="divide-y divide-zinc-100">
                  {lowStock.map((item) => {
                    const qty = Number(item.quantity_on_hand ?? item.quantity ?? 0);
                    return (
                      <Link
                        key={item.id}
                        to="/inventory"
                        className="flex items-center justify-between py-3 first:pt-0 last:pb-0 hover:bg-zinc-50/60 rounded px-2 -mx-2 transition-colors"
                      >
                        <div className="min-w-0">
                          <div className="text-sm font-semibold text-zinc-900 truncate">
                            {item.product_name || item.sku}
                          </div>
                          <div className="text-[11px] text-zinc-400 mt-0.5 truncate">
                            {item.sku} · {item.unit}
                          </div>
                        </div>
                        <Badge variant="warning" size="sm" className="shrink-0 ml-3">
                          {qty} left
                        </Badge>
                      </Link>
                    );
                  })}
                </div>
              ) : (
                <EmptyState
                  title="All stock levels healthy"
                  description="Low stock alerts and yardage warnings will be flagged here as inventory depletes."
                />
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </PageContainer>
  );
}
