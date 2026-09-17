import { useState } from 'react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Select,
  Card,
  CardContent,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Badge,
} from '../../components/ui/index.ts';
import { formatDate, formatQuantity } from '../../lib/formatters.ts';

const SAMPLE_DATE_1 = '2026-03-17T11:45:00.000Z';
const SAMPLE_DATE_2 = '2026-03-17T10:15:00.000Z';

export function StockMovementsPage() {
  const [filterType, setFilterType] = useState('all');

  return (
    <PageContainer>
      <PageHeader
        title="Stock Movements Ledger"
        description="Immutable audit trail of purchases, customer cuts, returns, and inventory adjustments."
        breadcrumbs={[
          { label: 'Inventory', href: '/inventory' },
          { label: 'Stock Movements' },
        ]}
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <div className="flex items-center gap-3">
            <div className="w-full sm:w-56">
              <Select
                value={filterType}
                onChange={(e) => setFilterType(e.target.value)}
              >
                <option value="all">All Movement Types</option>
                <option value="purchase_in">Purchase Inward (+)</option>
                <option value="sale_out">POS Sale Cut (-)</option>
                <option value="return_in">Customer Return (+)</option>
                <option value="adjustment">Stock Audit Adjustment</option>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Timestamp</TableHead>
            <TableHead>SKU</TableHead>
            <TableHead>Product / Material</TableHead>
            <TableHead>Movement Type</TableHead>
            <TableHead align="right">Qty Delta</TableHead>
            <TableHead>Reference / Invoice</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell className="text-xs text-zinc-600">
              {formatDate(SAMPLE_DATE_1, true)}
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-500">
              COT-LAWN-01-NAVY
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Egyptian Lawn - Plain
            </TableCell>
            <TableCell>
              <Badge variant="success" size="sm">
                Purchase In
              </Badge>
            </TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-emerald-700">
              +{formatQuantity(100, 'meters')}
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-600">
              PO-2026-0042
            </TableCell>
          </TableRow>

          <TableRow>
            <TableCell className="text-xs text-zinc-600">
              {formatDate(SAMPLE_DATE_2, true)}
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-500">
              SUIT-WOL-04-BLK
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Italian Wool Blend 120s
            </TableCell>
            <TableCell>
              <Badge variant="neutral" size="sm">
                Sale Out
              </Badge>
            </TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-rose-700">
              -{formatQuantity(4.25, 'meters')}
            </TableCell>
            <TableCell className="font-mono text-xs text-zinc-600">
              INV-10892
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </PageContainer>
  );
}
