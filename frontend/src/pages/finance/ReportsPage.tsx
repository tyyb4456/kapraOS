import { useState, useEffect } from 'react';
import { Download } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Button,
  Select,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Tabs,
  TabList,
  TabTrigger,
  TabContent,
  Skeleton,
} from '../../components/ui/index.ts';
import { formatCurrency } from '../../lib/formatters.ts';
import { getFinancialSummary } from '../../lib/api/reports.ts';
import type { FinancialSummary } from '../../types/index.ts';

export function ReportsPage() {
  const [period, setPeriod] = useState('this_month');
  const [summary, setSummary] = useState<FinancialSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadSummary = async () => {
      try {
        setLoading(true);
        const data = await getFinancialSummary();
        setSummary(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load financial summary');
      } finally {
        setLoading(false);
      }
    };
    loadSummary();
  }, []);

  return (
    <>
      {error && (
        <div className="mb-4 p-3 bg-rose-50 border border-rose-200 rounded-md text-sm text-rose-700">
          {error}
        </div>
      )}
      <PageContainer>
        <PageHeader
        title="Financial & Operational Reports"
        description="Review Profit & Loss, Cost of Goods Sold (COGS), Sales margins, and Balance Sheet position."
        breadcrumbs={[
          { label: 'Finance', href: '/reports' },
          { label: 'Reports' },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <Select
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              className="w-40"
            >
              <option value="today">Today</option>
              <option value="this_week">This Week</option>
              <option value="this_month">This Month</option>
              <option value="this_year">This Fiscal Year</option>
            </Select>
            <Button variant="outline" size="sm" leftIcon={<Download />} >
              Export PDF
            </Button>
          </div>
        }
      />

      {/* High-Level P&L Summary Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs font-medium text-zinc-500">
              Total Revenue (Sales)
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            {loading ? (
              <Skeleton className="h-8 w-32" />
            ) : (
              <>
                <div className="text-xl font-bold font-tabular text-zinc-900">
                  {summary ? formatCurrency(summary.total_sales) : formatCurrency(0)}
                </div>
                <p className="text-[11px] text-zinc-400 mt-1">Gross billed counter volume</p>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs font-medium text-zinc-500">
              Cost of Goods Sold (COGS)
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            {loading ? (
              <Skeleton className="h-8 w-32" />
            ) : (
              <>
                <div className="text-xl font-bold font-tabular text-zinc-900">
                  {summary ? formatCurrency(summary.total_cogs) : formatCurrency(0)}
                </div>
                <p className="text-[11px] text-zinc-400 mt-1">Weighted purchase cost of cuts sold</p>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs font-medium text-zinc-500">
              Operating Overheads
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            {loading ? (
              <Skeleton className="h-8 w-32" />
            ) : (
              <>
                <div className="text-xl font-bold font-tabular text-zinc-900">
                  {summary ? formatCurrency(summary.total_expenses) : formatCurrency(0)}
                </div>
                <p className="text-[11px] text-zinc-400 mt-1">Rent, electricity, staff & bags</p>
              </>
            )}
          </CardContent>
        </Card>

        <Card className="bg-zinc-900 text-white border-zinc-900">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs font-medium text-zinc-400">
              Estimated Net Profit
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            {loading ? (
              <Skeleton className="h-8 w-32" />
            ) : (
              <>
                <div className="text-xl font-bold font-tabular text-white">
                  {summary ? formatCurrency(summary.net_profit) : formatCurrency(0)}
                </div>
                <p className="text-[11px] text-zinc-400 mt-1">Revenue - COGS - Expenses</p>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Reports Section Tabs */}
      <Tabs defaultValue="pl">
        <TabList>
          <TabTrigger value="pl">Income Statement (P&L)</TabTrigger>
          <TabTrigger value="balance">Khata & Balance Sheet</TabTrigger>
          <TabTrigger value="cogs">COGS Breakdown</TabTrigger>
        </TabList>

        <TabContent value="pl">
          <Card className="mt-3">
            <CardContent className="p-6">
              {loading ? (
                <Skeleton className="h-32 w-full" />
              ) : summary ? (
                <div className="space-y-4 text-sm">
                  <div className="border-b border-zinc-100 pb-4">
                    <h4 className="font-semibold text-zinc-900 mb-3">Revenue</h4>
                    <div className="flex justify-between">
                      <span className="text-zinc-600">Total Sales</span>
                      <span className="font-tabular font-medium text-zinc-900">{formatCurrency(summary.total_sales)}</span>
                    </div>
                  </div>
                  <div className="border-b border-zinc-100 pb-4">
                    <h4 className="font-semibold text-zinc-900 mb-3">Cost of Goods Sold</h4>
                    <div className="flex justify-between">
                      <span className="text-zinc-600">COGS</span>
                      <span className="font-tabular font-medium text-zinc-900">{formatCurrency(summary.total_cogs)}</span>
                    </div>
                  </div>
                  <div className="border-b border-zinc-100 pb-4">
                    <h4 className="font-semibold text-zinc-900 mb-3">Gross Profit</h4>
                    <div className="flex justify-between">
                      <span className="text-zinc-600">Gross Profit</span>
                      <span className="font-tabular font-medium text-emerald-700">{formatCurrency(summary.gross_profit)}</span>
                    </div>
                  </div>
                  <div className="border-b border-zinc-100 pb-4">
                    <h4 className="font-semibold text-zinc-900 mb-3">Operating Expenses</h4>
                    <div className="flex justify-between">
                      <span className="text-zinc-600">Total Expenses</span>
                      <span className="font-tabular font-medium text-zinc-900">{formatCurrency(summary.total_expenses)}</span>
                    </div>
                  </div>
                  <div className="bg-zinc-50 rounded-lg p-4">
                    <h4 className="font-semibold text-zinc-900 mb-3">Net Profit</h4>
                    <div className="flex justify-between text-lg">
                      <span className="font-bold text-zinc-900">Net Profit</span>
                      <span className={`font-tabular font-bold ${summary.net_profit >= 0 ? 'text-emerald-700' : 'text-rose-700'}`}>
                        {formatCurrency(summary.net_profit)}
                      </span>
                    </div>
                  </div>
                </div>
              ) : (
                <p className="text-center text-zinc-500 text-xs">No data available</p>
              )}
            </CardContent>
          </Card>
        </TabContent>

        <TabContent value="balance">
          <Card className="mt-3">
            <CardContent className="p-6">
              {loading ? (
                <Skeleton className="h-32 w-full" />
              ) : summary ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div className="bg-emerald-50 border border-emerald-100 rounded-lg p-4">
                    <h4 className="font-semibold text-emerald-900 mb-2">Receivables (Customer Khata)</h4>
                    <div className="text-2xl font-bold font-tabular text-emerald-700">
                      {formatCurrency(summary.receivables)}
                    </div>
                    <p className="text-xs text-emerald-600 mt-1">Amount customers owe</p>
                  </div>
                  <div className="bg-rose-50 border border-rose-100 rounded-lg p-4">
                    <h4 className="font-semibold text-rose-900 mb-2">Payables (Supplier Khata)</h4>
                    <div className="text-2xl font-bold font-tabular text-rose-700">
                      {formatCurrency(summary.payables)}
                    </div>
                    <p className="text-xs text-rose-600 mt-1">Amount owed to suppliers</p>
                  </div>
                </div>
              ) : (
                <p className="text-center text-zinc-500 text-xs">No data available</p>
              )}
            </CardContent>
          </Card>
        </TabContent>

        <TabContent value="cogs">
          <Card className="mt-3">
            <CardContent className="p-6 text-center text-zinc-500 text-xs">
              Fabric inventory consumption tracking and weighted average cost calculations.
              COGS breakdown by product category will be available in future updates.
            </CardContent>
          </Card>
        </TabContent>
      </Tabs>
      </PageContainer>
    </>
  );
}