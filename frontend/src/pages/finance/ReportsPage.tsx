import { useState } from 'react';
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
} from '../../components/ui/index.ts';
import { formatCurrency } from '../../lib/formatters.ts';

export function ReportsPage() {
  const [period, setPeriod] = useState('this_month');

  return (
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
            <Button
              variant="outline"
              size="sm"
              leftIcon={<Download className="w-4 h-4" />}
            >
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
            <div className="text-xl font-bold font-tabular text-zinc-900">
              {formatCurrency(0)}
            </div>
            <p className="text-[11px] text-zinc-400 mt-1">Gross billed counter volume</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs font-medium text-zinc-500">
              Cost of Goods Sold (COGS)
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <div className="text-xl font-bold font-tabular text-zinc-900">
              {formatCurrency(0)}
            </div>
            <p className="text-[11px] text-zinc-400 mt-1">Weighted purchase cost of cuts sold</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs font-medium text-zinc-500">
              Operating Overheads
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <div className="text-xl font-bold font-tabular text-zinc-900">
              {formatCurrency(0)}
            </div>
            <p className="text-[11px] text-zinc-400 mt-1">Rent, electricity, staff & bags</p>
          </CardContent>
        </Card>

        <Card className="bg-zinc-900 text-white border-zinc-900">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs font-medium text-zinc-400">
              Estimated Net Profit
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <div className="text-xl font-bold font-tabular text-white">
              {formatCurrency(0)}
            </div>
            <p className="text-[11px] text-zinc-400 mt-1">Revenue - COGS - Expenses</p>
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
            <CardContent className="p-6 text-center text-zinc-500 text-xs">
              Income statement metrics will be rendered with real ledger totals in Step 14.
            </CardContent>
          </Card>
        </TabContent>

        <TabContent value="balance">
          <Card className="mt-3">
            <CardContent className="p-6 text-center text-zinc-500 text-xs">
              Receivables and supplier payables summaries are synchronized from the General Ledger.
            </CardContent>
          </Card>
        </TabContent>

        <TabContent value="cogs">
          <Card className="mt-3">
            <CardContent className="p-6 text-center text-zinc-500 text-xs">
              Fabric inventory consumption tracking and weighted average cost calculations.
            </CardContent>
          </Card>
        </TabContent>
      </Tabs>
    </PageContainer>
  );
}
