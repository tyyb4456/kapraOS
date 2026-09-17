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
import { formatCurrency } from '../lib/formatters.ts';

export function DashboardPage() {
  const { user } = useAuth();

  const getGreeting = () => {
    const hour = new Date().getHours();
    if (hour < 12) return 'Good morning';
    if (hour < 18) return 'Good afternoon';
    return 'Good evening';
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
                Today's Sales
              </CardTitle>
              <div className="w-7 h-7 rounded-md bg-emerald-50 text-emerald-600 flex items-center justify-center">
                <TrendingUp className="w-4 h-4" />
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              <div className="text-xl font-bold font-tabular text-zinc-900">
                {formatCurrency(0)}
              </div>
              <div className="flex items-center gap-1.5 mt-1">
                <Badge variant="neutral" size="sm">
                  0 transactions
                </Badge>
                <span className="text-[11px] text-zinc-400">Recorded today</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2 border-none">
              <CardTitle className="text-xs font-medium text-zinc-500">
                Customer Payments
              </CardTitle>
              <div className="w-7 h-7 rounded-md bg-sky-50 text-sky-600 flex items-center justify-center">
                <CreditCard className="w-4 h-4" />
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              <div className="text-xl font-bold font-tabular text-zinc-900">
                {formatCurrency(0)}
              </div>
              <div className="flex items-center gap-1.5 mt-1">
                <Badge variant="neutral" size="sm">
                  Cash & Khata
                </Badge>
                <span className="text-[11px] text-zinc-400">Collections</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2 border-none">
              <CardTitle className="text-xs font-medium text-zinc-500">
                Purchases Inward
              </CardTitle>
              <div className="w-7 h-7 rounded-md bg-zinc-100 text-zinc-700 flex items-center justify-center">
                <ShoppingBag className="w-4 h-4" />
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              <div className="text-xl font-bold font-tabular text-zinc-900">
                {formatCurrency(0)}
              </div>
              <div className="flex items-center gap-1.5 mt-1">
                <Badge variant="neutral" size="sm">
                  0 shipments
                </Badge>
                <span className="text-[11px] text-zinc-400">Inward inventory</span>
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
                {formatCurrency(0)}
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
              <EmptyState
                title="All stock levels healthy"
                description="Low stock alerts and yardage warnings will be flagged here as inventory depletes."
              />
            </CardContent>
          </Card>
        </div>
      </div>
    </PageContainer>
  );
}
