import { useEffect, useMemo, useState } from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Button,
  Input,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Badge,
  Alert,
  Skeleton,
} from '../../components/ui/index.ts';
import { formatCurrency, formatDate } from '../../lib/formatters.ts';
import { getSalesTrend } from '../../lib/api/analytics.ts';
import { useTheme } from '../../context/ThemeContext.tsx';
import type { SalesTrend } from '../../types/index.ts';

type PresetKey = '7d' | '30d' | '90d' | '12m' | 'custom';

const PRESETS: Array<{ key: PresetKey; label: string; days: number | null }> = [
  { key: '7d', label: 'Last 7 days', days: 7 },
  { key: '30d', label: 'Last 30 days', days: 30 },
  { key: '90d', label: 'Last 90 days', days: 90 },
  { key: '12m', label: 'Last 12 months', days: 365 },
  { key: 'custom', label: 'Custom', days: null },
];

function startOfDayUTC(d: Date): Date {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
}

function isoDaysAgo(days: number): string {
  const now = new Date();
  const start = startOfDayUTC(now);
  start.setUTCDate(start.getUTCDate() - (days - 1));
  return start.toISOString();
}

function toISODateInput(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function endOfDayUTC(dateInput: string): string {
  return new Date(`${dateInput}T23:59:59.999Z`).toISOString();
}

const compactMoney = (v: number | string): string => {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n)) return '0';
  return new Intl.NumberFormat('en-PK', { notation: 'compact' }).format(n);
};

const moneyTooltip = (value: unknown): string => formatCurrency(Number(value ?? 0));

export function AnalyticsPage() {
  const { isDark } = useTheme();
  const [preset, setPreset] = useState<PresetKey>('30d');
  const [customStart, setCustomStart] = useState(() => {
    const d = startOfDayUTC(new Date());
    d.setUTCDate(d.getUTCDate() - 29);
    return toISODateInput(d);
  });
  const [customEnd, setCustomEnd] = useState(() => toISODateInput(new Date()));
  const [appliedCustom, setAppliedCustom] = useState<{ start: string; end: string } | null>(null);

  const [trend, setTrend] = useState<SalesTrend | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        let params: { start_date?: string; end_date?: string };
        if (preset === 'custom') {
          if (!appliedCustom) {
            setLoading(false);
            return;
          }
          const s = new Date(`${appliedCustom.start}T00:00:00.000Z`);
          const e = new Date(appliedCustom.end);
          if (isNaN(s.getTime()) || isNaN(e.getTime()) || s > e) {
            setError('Custom range is invalid: start must be on or before end.');
            setLoading(false);
            return;
          }
          if ((e.getTime() - s.getTime()) / 86400000 > 400) {
            setError('Custom range is too wide: maximum is 400 days.');
            setLoading(false);
            return;
          }
          params = {
            start_date: s.toISOString(),
            end_date: endOfDayUTC(appliedCustom.end),
          };
        } else {
          const days = PRESETS.find((p) => p.key === preset)?.days ?? 30;
          params = { start_date: isoDaysAgo(days) };
        }
        const data = await getSalesTrend(params);
        if (!cancelled) setTrend(data);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load analytics');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [preset, appliedCustom]);

  const chartData = useMemo(() => {
    if (!trend) return [];
    return trend.buckets.map((b) => {
      const d = new Date(b.bucket_start);
      const short =
        trend.granularity === 'month'
          ? d.toLocaleDateString('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' })
          : trend.granularity === 'week'
            ? `w/c ${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })}`
            : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
      return {
        label: short,
        full: formatDate(b.bucket_start),
        revenue: b.revenue,
        gross_profit: b.gross_profit,
        net_profit: b.net_profit,
        sales_count: b.sales_count,
      };
    });
  }, [trend]);

  const tableRows = useMemo(() => {
    if (!trend) return [];
    return [...trend.buckets].reverse();
  }, [trend]);

  const hasData = (trend?.total_sales_count ?? 0) > 0 || (trend?.total_revenue ?? 0) > 0;

  // Recharts renders raw SVG with hardcoded paints — derive them from the
  // active theme so grids, ticks, tooltips and dark-on-dark series stay
  // readable in both modes.
  const chartTheme = useMemo(
    () => ({
      grid: isDark ? '#353c4a' : '#e4e4e7',
      tick: isDark ? '#a1a1aa' : '#71717a',
      axisLine: isDark ? '#4b5468' : '#d4d4d8',
      netProfit: isDark ? '#fafafa' : '#18181b',
      bar: isDark ? '#e4e4e7' : '#18181b',
      tooltipBg: isDark ? '#282e38' : '#ffffff',
      tooltipBorder: isDark ? '#4b5468' : '#e4e4e7',
      tooltipText: isDark ? '#f4f4f5' : '#18181b',
      tooltipMuted: isDark ? '#a1a1aa' : '#71717a',
    }),
    [isDark],
  );

  return (
    <PageContainer>
      <PageHeader
        title="Sales Analytics"
        description="Revenue, profit and order trends for any window — pick a preset or choose custom dates."
        breadcrumbs={[{ label: 'Finance', href: '/reports' }, { label: 'Analytics' }]}
        actions={
          trend && !loading ? (
            <Badge variant="neutral" size="sm">
              {trend.granularity === 'day' ? 'Daily' : trend.granularity === 'week' ? 'Weekly' : 'Monthly'} buckets · {trend.buckets.length} points
            </Badge>
          ) : undefined
        }
      />

      {error && (
        <Alert variant="danger" title="Could not load analytics">
          {error}
        </Alert>
      )}

      {/* Range presets */}
      <Card>
        <CardContent className="p-4">
          <div className="flex flex-wrap items-center gap-2">
            {PRESETS.map((p) => (
              <Button
                key={p.key}
                variant={preset === p.key ? 'primary' : 'outline'}
                size="sm"
                onClick={() => setPreset(p.key)}
              >
                {p.label}
              </Button>
            ))}
          </div>
          {preset === 'custom' && (
            <div className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_auto] gap-3 mt-3 items-end">
              <Input
                label="Start date"
                type="date"
                value={customStart}
                max={customEnd}
                onChange={(e) => setCustomStart(e.target.value)}
              />
              <Input
                label="End date"
                type="date"
                value={customEnd}
                min={customStart}
                max={toISODateInput(new Date())}
                onChange={(e) => setCustomEnd(e.target.value)}
              />
              <Button
                variant="primary"
                size="sm"
                onClick={() => setAppliedCustom({ start: customStart, end: customEnd })}
              >
                Apply
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Summary cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { title: 'Total Revenue', value: trend?.total_revenue, hint: 'Ledger sales in window' },
          { title: 'Gross Profit', value: trend?.total_gross_profit, hint: 'Revenue − COGS' },
          { title: 'Net Profit', value: trend?.total_net_profit, hint: 'Gross − expenses' },
          { title: 'Orders', value: trend?.total_sales_count, hint: 'Qualifying sales', money: false },
        ].map((c) => (
          <Card key={c.title}>
            <CardHeader className="p-4 pb-2">
              <CardTitle className="text-xs font-medium text-zinc-500 dark:text-zinc-400">{c.title}</CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              {loading ? (
                <Skeleton className="h-8 w-32" />
              ) : (
                <>
                  <div className="text-xl font-bold font-tabular text-zinc-900 dark:text-zinc-50">
                    {c.money === false
                      ? `${c.value ?? 0}`
                      : formatCurrency(c.value ?? 0)}
                  </div>
                  <p className="text-[11px] text-zinc-400 dark:text-zinc-500 mt-1">{c.hint}</p>
                </>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Revenue & profit chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Revenue & profit trend</CardTitle>
          <CardDescription>
            Ledger-derived revenue, gross profit and net profit per bucket.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <Skeleton className="h-64 w-full" />
          ) : !hasData ? (
            <p className="text-center text-zinc-500 dark:text-zinc-400 text-xs py-10">
              No sales in this window yet — record a sale and the trend will appear here.
            </p>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} />
                  <XAxis
                    dataKey="label"
                    tick={{ fontSize: 11, fill: chartTheme.tick }}
                    tickLine={{ stroke: chartTheme.axisLine }}
                    axisLine={{ stroke: chartTheme.axisLine }}
                    tickMargin={8}
                    minTickGap={24}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: chartTheme.tick }}
                    tickLine={{ stroke: chartTheme.axisLine }}
                    axisLine={{ stroke: chartTheme.axisLine }}
                    tickFormatter={compactMoney}
                    width={52}
                  />
                  <Tooltip
                    formatter={moneyTooltip}
                    labelFormatter={(_, payload) => payload?.[0]?.payload?.full ?? ''}
                    contentStyle={{
                      backgroundColor: chartTheme.tooltipBg,
                      borderColor: chartTheme.tooltipBorder,
                      borderRadius: 8,
                      fontSize: 12,
                      color: chartTheme.tooltipText,
                    }}
                    labelStyle={{ color: chartTheme.tooltipText, fontWeight: 600 }}
                    itemStyle={{ color: chartTheme.tooltipText }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12, color: chartTheme.tick }} />
                  <Area type="monotone" dataKey="revenue" name="Revenue" stroke="#059669" fill="#059669" fillOpacity={isDark ? 0.22 : 0.14} strokeWidth={2} />
                  <Area type="monotone" dataKey="gross_profit" name="Gross profit" stroke="#0284c7" fill="#0284c7" fillOpacity={isDark ? 0.18 : 0.1} strokeWidth={2} />
                  <Area type="monotone" dataKey="net_profit" name="Net profit" stroke={chartTheme.netProfit} fill={chartTheme.netProfit} fillOpacity={isDark ? 0.14 : 0.08} strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Orders chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Orders per bucket</CardTitle>
          <CardDescription>Count of qualifying sales in each bucket.</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <Skeleton className="h-48 w-full" />
          ) : !hasData ? (
            <p className="text-center text-zinc-500 dark:text-zinc-400 text-xs py-10">
              No orders in this window.
            </p>
          ) : (
            <div className="h-48 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} />
                  <XAxis
                    dataKey="label"
                    tick={{ fontSize: 11, fill: chartTheme.tick }}
                    tickLine={{ stroke: chartTheme.axisLine }}
                    axisLine={{ stroke: chartTheme.axisLine }}
                    tickMargin={8}
                    minTickGap={24}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: chartTheme.tick }}
                    tickLine={{ stroke: chartTheme.axisLine }}
                    axisLine={{ stroke: chartTheme.axisLine }}
                    allowDecimals={false}
                    width={36}
                  />
                  <Tooltip
                    labelFormatter={(_, payload) => payload?.[0]?.payload?.full ?? ''}
                    contentStyle={{
                      backgroundColor: chartTheme.tooltipBg,
                      borderColor: chartTheme.tooltipBorder,
                      borderRadius: 8,
                      fontSize: 12,
                      color: chartTheme.tooltipText,
                    }}
                    labelStyle={{ color: chartTheme.tooltipText, fontWeight: 600 }}
                    itemStyle={{ color: chartTheme.tooltipText }}
                    cursor={{ fill: isDark ? '#353c4a' : '#f4f4f5' }}
                  />
                  <Bar dataKey="sales_count" name="Orders" fill={chartTheme.bar} radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Written breakdown */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Period breakdown</CardTitle>
          <CardDescription>Same buckets as the charts, latest first.</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="p-6 space-y-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : tableRows.length === 0 ? (
            <p className="text-center text-zinc-500 dark:text-zinc-400 text-xs p-6">No data available</p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Period</TableHead>
                    <TableHead align="right">Revenue</TableHead>
                    <TableHead align="right">COGS</TableHead>
                    <TableHead align="right">Expenses</TableHead>
                    <TableHead align="right">Gross</TableHead>
                    <TableHead align="right">Net</TableHead>
                    <TableHead align="right">Orders</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tableRows.map((b) => (
                    <TableRow key={b.bucket_start}>
                      <TableCell>{formatDate(b.bucket_start)}</TableCell>
                      <TableCell align="right" className="font-tabular">{formatCurrency(b.revenue)}</TableCell>
                      <TableCell align="right" className="font-tabular">{formatCurrency(b.cogs)}</TableCell>
                      <TableCell align="right" className="font-tabular">{formatCurrency(b.expenses)}</TableCell>
                      <TableCell align="right" className="font-tabular text-emerald-700 dark:text-emerald-300">{formatCurrency(b.gross_profit)}</TableCell>
                      <TableCell align="right" className={`font-tabular font-medium ${b.net_profit >= 0 ? 'text-emerald-700 dark:text-emerald-300' : 'text-rose-700 dark:text-rose-300'}`}>
                        {formatCurrency(b.net_profit)}
                      </TableCell>
                      <TableCell align="right" className="font-tabular">{b.sales_count}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </PageContainer>
  );
}
