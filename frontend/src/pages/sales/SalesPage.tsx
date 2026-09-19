import { useState, useEffect } from 'react';
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
  Skeleton,
} from '../../components/ui/index.ts';
import { formatCurrency, formatDate } from '../../lib/formatters.ts';
import { getSales } from '../../lib/api/sales.ts';
import type { Sale } from '../../types/index.ts';

export function SalesPage() {
  const [searchTerm, setSearchTerm] = useState('');
  const [sales, setSales] = useState<Sale[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadSales = async () => {
      try {
        setLoading(true);
        const data = await getSales();
        setSales(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load sales');
      } finally {
        setLoading(false);
      }
    };
    loadSales();
  }, []);

  const filteredSales = sales.filter((sale) => {
    if (!searchTerm) return true;
    const term = searchTerm.toLowerCase();
    return (
      (sale.invoice_number ?? '').toLowerCase().includes(term) ||
      (sale.customer_name ?? '').toLowerCase().includes(term)
    );
  });

  const getPaymentMethodBadge = (method: string) => {
    switch (method) {
      case 'cash':
        return <Badge variant="neutral" size="sm">Cash</Badge>;
      case 'card':
        return <Badge variant="secondary" size="sm">Card</Badge>;
      case 'bank':
        return <Badge variant="secondary" size="sm">Bank Transfer</Badge>;
      case 'jazzcash':
        return <Badge variant="secondary" size="sm">JazzCash</Badge>;
      case 'easypaisa':
        return <Badge variant="secondary" size="sm">Easypaisa</Badge>;
      case 'other':
        return <Badge variant="secondary" size="sm">Other</Badge>;
      default:
        return <Badge variant="neutral" size="sm">{method}</Badge>;
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'completed':
        return <Badge variant="success" size="sm">Completed</Badge>;
      case 'partial':
        return <Badge variant="warning" size="sm">Partial</Badge>;
      case 'cancelled':
        return <Badge variant="destructive" size="sm">Cancelled</Badge>;
      case 'returned':
        return <Badge variant="secondary" size="sm">Returned</Badge>;
      default:
        return <Badge variant="neutral" size="sm">{status}</Badge>;
    }
  };

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

      {error && (
        <div className="mb-4 p-3 bg-rose-50 border border-rose-200 rounded-md text-sm text-rose-700">
          {error}
        </div>
      )}

      <Card>
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
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-40" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-16" /></TableCell>
                </TableRow>
              ))
            ) : filteredSales.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center py-8 text-zinc-500">
                  {searchTerm ? 'No matching sales found' : 'No sales recorded yet'}
                </TableCell>
              </TableRow>
            ) : (
              filteredSales.map((sale) => (
                <TableRow key={sale.id}>
                  <TableCell className="font-mono text-xs font-medium text-zinc-900">
                    {sale.invoice_number || '—'}
                  </TableCell>
                  <TableCell className="text-xs text-zinc-600">
                    {formatDate(sale.created_at, true)}
                  </TableCell>
                  <TableCell className="font-medium text-zinc-900">
                    {sale.customer_name || 'Walk-in Customer'}
                  </TableCell>
                  <TableCell>{getPaymentMethodBadge(sale.payment_method)}</TableCell>
                  <TableCell align="right" className="font-tabular text-zinc-600">
                    {formatCurrency(sale.subtotal)}
                  </TableCell>
                  <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
                    {formatCurrency(sale.total_amount)}
                  </TableCell>
                  <TableCell>{getStatusBadge(sale.status)}</TableCell>
                  <TableCell align="right">
                    <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                      Print Bill
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>
    </PageContainer>
  );
}