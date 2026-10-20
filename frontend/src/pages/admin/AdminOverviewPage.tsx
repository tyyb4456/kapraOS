import { useEffect, useState } from 'react';
import {
  ShieldCheck,
  Users,
  Store,
  Receipt,
  ShoppingCart,
  Wallet,
  UserPlus,
  Activity,
  Info,
} from 'lucide-react';
import { PageContainer, PageHeader } from '../../components/layout/index.ts';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
  Badge,
  Skeleton,
  Alert,
  EmptyState,
} from '../../components/ui/index.ts';
import { getAdminHealth, getAdminOverview } from '../../lib/api/admin.ts';
import { formatDate } from '../../lib/formatters.ts';
import type {
  AdminDailyCount,
  AdminHealth,
  AdminOverview,
} from '../../types/index.ts';

function StatCard({
  label,
  value,
  sub,
  icon,
}: {
  label: string;
  value: string;
  sub?: string;
  icon: React.ReactNode;
}) {
  return (
    <Card>
      <CardContent className="p-4 flex items-start gap-3">
        <div className="clay-icon-tile w-9 h-9 shrink-0 bg-[var(--accent-soft)] text-[var(--accent-ink)] dark:text-[var(--accent)]">
          {icon}
        </div>
        <div className="min-w-0">
          <p className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            {label}
          </p>
          <p className="text-xl font-bold font-tabular text-zinc-900 dark:text-zinc-50">
            {value}
          </p>
          {sub && (
            <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-0.5">{sub}</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function TrendList({
  title,
  description,
  points,
  emptyLabel,
}: {
  title: string;
  description: string;
  points: AdminDailyCount[];
  emptyLabel: string;
}) {
  const max = Math.max(1, ...points.map((p) => p.count));
  const recent = [...points].slice(-14).reverse();

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="p-4">
        {recent.length === 0 ? (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">{emptyLabel}</p>
        ) : (
          <div className="space-y-1.5">
            {recent.map((point) => (
              <div key={point.day} className="flex items-center gap-2 text-xs">
                <span className="w-24 shrink-0 text-zinc-500 dark:text-zinc-400 font-tabular">
                  {formatDate(point.day)}
                </span>
                <div className="flex-1 h-2 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-[var(--accent)]"
                    style={{ width: `${Math.max(4, (point.count / max) * 100)}%` }}
                  />
                </div>
                <span className="w-8 text-right font-tabular font-medium text-zinc-700 dark:text-zinc-300">
                  {point.count}
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function AdminOverviewPage() {
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const [overviewData, healthData] = await Promise.all([
          getAdminOverview(),
          getAdminHealth(),
        ]);
        if (cancelled) return;
        setOverview(overviewData);
        setHealth(healthData);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load platform overview');
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

  return (
    <PageContainer>
      <PageHeader
        title="Platform Overview"
        description="Platform-wide KPIs across every shop on KapraOS. No shop-level business records are shown here."
        breadcrumbs={[{ label: 'Platform' }, { label: 'Overview' }]}
        actions={
          <Badge variant="neutral" size="sm">
            <ShieldCheck className="w-3.5 h-3.5" />
            Platform Admin
          </Badge>
        }
      />

      {error && (
        <Alert variant="danger" title="Could not load platform data">
          {error}
        </Alert>
      )}

      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="p-4 space-y-2">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="h-6 w-16" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : overview ? (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard
              label="Total Users"
              value={String(overview.total_users)}
              sub={`${overview.total_owners} owners · ${overview.total_staff} staff`}
              icon={<Users className="w-4 h-4" />}
            />
            <StatCard
              label="Total Shops"
              value={String(overview.total_shops)}
              sub={`+${overview.new_shops_last_7_days} in last 7 days`}
              icon={<Store className="w-4 h-4" />}
            />
            <StatCard
              label="Platform Admins"
              value={String(overview.platform_admin_count)}
              sub="Server-side flag holders"
              icon={<ShieldCheck className="w-4 h-4" />}
            />
            <StatCard
              label="New Users"
              value={String(overview.new_users_last_7_days)}
              sub={`last 7 days · ${overview.new_users_last_30_days} in 30 days`}
              icon={<UserPlus className="w-4 h-4" />}
            />
            <StatCard
              label="Sales"
              value={String(overview.total_sales)}
              sub={`${overview.sales_last_30_days} in last 30 days`}
              icon={<Receipt className="w-4 h-4" />}
            />
            <StatCard
              label="Purchases"
              value={String(overview.total_purchases)}
              sub={`${overview.purchases_last_30_days} in last 30 days`}
              icon={<ShoppingCart className="w-4 h-4" />}
            />
            <StatCard
              label="Payments"
              value={String(overview.total_payments)}
              sub={`${overview.payments_last_30_days} in last 30 days`}
              icon={<Wallet className="w-4 h-4" />}
            />
            <StatCard
              label="Customers / Suppliers / Products"
              value={`${overview.total_customers} / ${overview.total_suppliers} / ${overview.total_products}`}
              sub="Platform-wide record counts"
              icon={<Activity className="w-4 h-4" />}
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <TrendList
              title="User Registrations"
              description="Daily new users over the last 30 days (most recent first)."
              points={overview.user_registrations_by_day}
              emptyLabel="No user registrations in the last 30 days."
            />
            <TrendList
              title="Shop Creations"
              description="Daily new shops over the last 30 days (most recent first)."
              points={overview.shop_creations_by_day}
              emptyLabel="No new shops in the last 30 days."
            />
          </div>

          {overview.unavailable_metrics.length > 0 && (
            <Alert variant="info" title="Metrics not available on this platform">
              <ul className="list-disc list-inside space-y-1">
                {overview.unavailable_metrics.map((metric) => (
                  <li key={metric.key}>
                    <span className="font-medium">{metric.key}</span>: {metric.reason}
                  </li>
                ))}
              </ul>
            </Alert>
          )}
        </>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Platform Health</CardTitle>
          <CardDescription>
            Live dependency checks{health ? ` · checked ${formatDate(health.checked_at, true)}` : ''}
          </CardDescription>
        </CardHeader>
        <CardContent className="p-4">
          {loading ? (
            <div className="space-y-2">
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-4 w-64" />
            </div>
          ) : health ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <span className="text-xs text-zinc-500 dark:text-zinc-400">Overall</span>
                <Badge variant={health.status === 'ok' ? 'success' : 'warning'} size="sm">
                  {health.status}
                </Badge>
              </div>
              <div className="divide-y divide-[var(--clay-border-soft)]">
                {health.checks.map((check) => (
                  <div key={check.name} className="flex items-center justify-between py-2 gap-3">
                    <div className="min-w-0">
                      <p className="text-xs font-medium text-zinc-800 dark:text-zinc-200">
                        {check.name}
                      </p>
                      {check.detail && (
                        <p className="text-[11px] text-zinc-500 dark:text-zinc-400 truncate">
                          {check.detail}
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {check.latency_ms !== null && (
                        <span className="text-[11px] font-tabular text-zinc-500 dark:text-zinc-400">
                          {check.latency_ms.toFixed(1)} ms
                        </span>
                      )}
                      <Badge variant={check.status === 'ok' ? 'success' : 'destructive'} size="sm">
                        {check.status}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <EmptyState
              icon={<Info className="w-5 h-5" />}
              title="Health unavailable"
              description="The platform health endpoint could not be reached."
            />
          )}
        </CardContent>
      </Card>
    </PageContainer>
  );
}
