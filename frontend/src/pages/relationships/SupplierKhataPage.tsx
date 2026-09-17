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

const SAMPLE_DATE_1 = '2026-03-12T10:00:00.000Z';
const SAMPLE_DATE_2 = '2026-03-15T14:30:00.000Z';

export function SupplierKhataPage() {
  const [selectedSupplier, setSelectedSupplier] = useState('s1');

  return (
    <PageContainer>
      <PageHeader
        title="Supplier Khata Ledger (Payables)"
        description="Track inward consignments, mill billing, bank payments, and remaining balances owed."
        breadcrumbs={[
          { label: 'Relationships', href: '/suppliers' },
          { label: 'Supplier Khata' },
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
              Record Payment Out
            </Button>
          </div>
        }
      />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="sm:col-span-2">
          <Card>
            <CardContent className="p-4">
              <Select
                label="Select Supplier Account"
                value={selectedSupplier}
                onChange={(e) => setSelectedSupplier(e.target.value)}
              >
                <option value="s1">Kohinoor Textile Mills Ltd. (Phone: 042-3591234)</option>
                <option value="s2">Nishat Weaving Mills (Phone: 041-8765432)</option>
              </Select>
            </CardContent>
          </Card>
        </div>

        <Card className="bg-zinc-900 text-white border-zinc-900">
          <CardContent className="p-4 flex flex-col justify-center">
            <span className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
              Outstanding Payable
            </span>
            <div className="text-2xl font-bold font-tabular text-white mt-0.5">
              {formatCurrency(185000)}
            </div>
            <span className="text-[10px] text-zinc-400 mt-1">Shop owes supplier</span>
          </CardContent>
        </Card>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Date</TableHead>
            <TableHead>Consignment / Description</TableHead>
            <TableHead>PO / Bilty #</TableHead>
            <TableHead align="right">Credit (Bill +)</TableHead>
            <TableHead align="right">Debit (Paid -)</TableHead>
            <TableHead align="right">Running Payable</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell className="text-xs text-zinc-600">
              {formatDate(SAMPLE_DATE_1, false)}
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Consignment Inward (12 Rolls Lawn)
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-500">
              PO-2026-0042
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-900 font-semibold">
              {formatCurrency(390000)}
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-400">
              —
            </TableCell>
            <TableCell align="right" className="font-tabular font-bold text-zinc-900">
              {formatCurrency(390000)}
            </TableCell>
          </TableRow>

          <TableRow>
            <TableCell className="text-xs text-zinc-600">
              {formatDate(SAMPLE_DATE_2, false)}
            </TableCell>
            <TableCell className="font-medium text-emerald-800">
              Bank Transfer / Cheque Payment
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-500">
              TRF-88219
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-400">
              —
            </TableCell>
            <TableCell align="right" className="font-tabular text-emerald-700 font-semibold">
              {formatCurrency(205000)}
            </TableCell>
            <TableCell align="right" className="font-tabular font-bold text-zinc-900">
              {formatCurrency(185000)}
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </PageContainer>
  );
}
