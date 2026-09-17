import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Search } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Button,
  Input,
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
import { formatCurrency, formatDate } from '../../lib/formatters.ts';

const SAMPLE_DATE_1 = '2026-03-17T11:50:00.000Z';
const SAMPLE_DATE_2 = '2026-03-17T09:30:00.000Z';

export function SalesPage() {
  const [searchTerm, setSearchTerm] = useState('');

  return (
    <PageContainer>
      <PageHeader
        title="Sales & Counter Orders"
        description="View past receipts, cash transactions, Khata billing, and POS order details."
        breadcrumbs={[
          { label: 'Sales', href: '/sales' },
          { label: 'Orders' },
        ]}
        actions={
          <Link to="/sales/new">
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Plus className="w-4 h-4" />}
            >
              New Sale (POS)
            </Button>
          </Link>
        }
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <div className="flex flex-col sm:flex-row items-center gap-3">
            <div className="flex-1 w-full">
              <Input
                placeholder="Search by invoice number or customer name..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                leftIcon={<Search className="w-4 h-4" />}
              />
            </div>
            <div className="w-full sm:w-44">
              <Select defaultValue="all">
                <option value="all">All Payment Methods</option>
                <option value="cash">Cash</option>
                <option value="khata">Customer Khata</option>
                <option value="card">Card</option>
                <option value="bank_transfer">Bank Transfer</option>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Invoice #</TableHead>
            <TableHead>Date</TableHead>
            <TableHead>Customer</TableHead>
            <TableHead>Payment Method</TableHead>
            <TableHead align="right">Subtotal</TableHead>
            <TableHead align="right">Total Amount</TableHead>
            <TableHead>Status</TableHead>
            <TableHead align="right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell className="font-mono text-xs font-medium text-zinc-900">
              INV-10892
            </TableCell>
            <TableCell className="text-xs text-zinc-600">
              {formatDate(SAMPLE_DATE_1, true)}
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Chaudhry Fabric Traders
            </TableCell>
            <TableCell>
              <Badge variant="neutral" size="sm">
                Cash
              </Badge>
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-600">
              {formatCurrency(14450)}
            </TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
              {formatCurrency(14450)}
            </TableCell>
            <TableCell>
              <Badge variant="success" size="sm">
                Completed
              </Badge>
            </TableCell>
            <TableCell align="right">
              <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                Print Bill
              </Button>
            </TableCell>
          </TableRow>

          <TableRow>
            <TableCell className="font-mono text-xs font-medium text-zinc-900">
              INV-10891
            </TableCell>
            <TableCell className="text-xs text-zinc-600">
              {formatDate(SAMPLE_DATE_2, true)}
            </TableCell>
            <TableCell className="font-medium text-zinc-900">
              Haji Muhammad & Sons
            </TableCell>
            <TableCell>
              <Badge variant="warning" size="sm">
                Khata (Credit)
              </Badge>
            </TableCell>
            <TableCell align="right" className="font-tabular text-zinc-600">
              {formatCurrency(38500)}
            </TableCell>
            <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
              {formatCurrency(38500)}
            </TableCell>
            <TableCell>
              <Badge variant="success" size="sm">
                Completed
              </Badge>
            </TableCell>
            <TableCell align="right">
              <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                Print Bill
              </Button>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </PageContainer>
  );
}
