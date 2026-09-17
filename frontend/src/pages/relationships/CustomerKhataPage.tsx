import { useState } from 'react';
import { Plus, Download } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Button,
  Select,
  Card,
  CardContent,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '../../components/ui/index.ts';
import { formatCurrency, formatDate } from '../../lib/formatters.ts';

const SAMPLE_DATE_1 = '2026-03-15T10:00:00.000Z';
const SAMPLE_DATE_2 = '2026-03-16T15:30:00.000Z';

export function CustomerKhataPage() {
  const [selectedCustomer, setSelectedCustomer] = useState('c1');

  return (
    <PageContainer>
      <PageHeader
        title="Customer Khata Ledger (Receivables)"
        description="Track credit sales, partial cash collections, receipts, and outstanding khata balance."
        breadcrumbs={[
          { label: 'Relationships', href: '/customers' },
          { label: 'Customer Khata' },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              leftIcon={<Download className="w-4 h-4" />}
            >
              Export Statement
            </Button>
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Plus className="w-4 h-4" />}
            >
              Record Payment
            </Button>
          </div>
        }
      />

      {/* Customer Selector & Net Balance Banner */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="sm:col-span-2">
          <Card>
            <CardContent className="p-4">
              <Select
                label="Select Customer Account"
                value={selectedCustomer}
                onChange={(e) => setSelectedCustomer(e.target.value)}
              >
                <option value="c1">Chaudhry Fabric Traders (Phone: 0300-8472910)</option>
                <option value="c2">Haji Muhammad & Sons (Phone: 0321-4455667)</option>
              </Select>
            </CardContent>
          </Card>
        </div>

        <Card className="bg-zinc-900 text-white border-zinc-900">
          <CardContent className="p-4 flex flex-col justify-center">
            <span className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
              Outstanding Receivable
            </span>
            <div className="text-2xl font-bold font-tabular text-white mt-0.5">
              {formatCurrency(45000)}
            </div>
            <span className="text-[10px] text-zinc-400 mt-1">Customer owes store</span>
          </CardContent>
        </Card>
      </div>

      {/* Khata Ledger Table Shell */}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Date</TableHead>
            <TableHead>Transaction Description</TableHead>
            <TableHead>Ref / Invoice</TableHead>
            <TableHead align="right">Debit (Sale +)</TableHead>
            <TableHead align="right">Credit (Payment -)</TableHead>
            <TableHead align="right">Running Balance</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell className="text-xs text-zinc-600">
              {formatDate(SAMPLE_DATE_1, false)}
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Fabric Purchase on Credit (40m Lawn)
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-500">
              INV-10820
            </TableCell>
            <TableCell align="right" className="font-tabular text-rose-700 font-semibold">
              {formatCurrency(38000)}
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-400">
              —
            </TableCell>
            <TableCell align="right" className="font-tabular font-bold text-zinc-900">
              {formatCurrency(75000)}
            </TableCell>
          </TableRow>

          <TableRow>
            <TableCell className="text-xs text-zinc-600">
              {formatDate(SAMPLE_DATE_2, false)}
            </TableCell>
            <TableCell className="font-medium text-emerald-800">
              Cash Payment Received at Counter
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-500">
              RCP-4412
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-400">
              —
            </TableCell>
            <TableCell align="right" className="font-tabular text-emerald-700 font-semibold">
              {formatCurrency(30000)}
            </TableCell>
            <TableCell align="right" className="font-tabular font-bold text-zinc-900">
              {formatCurrency(45000)}
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </PageContainer>
  );
}
