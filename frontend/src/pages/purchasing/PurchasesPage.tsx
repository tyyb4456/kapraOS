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
import { getPurchases } from '../../lib/api/purchases.ts';
import type { Purchase } from '../../types/index.ts';

export function PurchasesPage() {
  const [searchTerm, setSearchTerm] = useState('');
  const [purchases, setPurchases] = useState<Purchase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadPurchases = async () => {
      try {
        setLoading(true);
        const data = await getPurchases();
        setPurchases(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load purchases');
      } finally {
        setLoading(false);
      }
    };
    loadPurchases();
  }, []);

  const filteredPurchases = purchases.filter(
    (purchase) =>
      purchase.order_number?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      purchase.supplier_name?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'received':
        return <Badge variant="success" size="sm">Received</Badge>;
      case 'pending':
        return <Badge variant="warning" size="sm">Pending Delivery</Badge>;
      case 'ordered':
        return <Badge variant="secondary" size="sm">Ordered</Badge>;
      case 'cancelled':
        return <Badge variant="destructive" size="sm">Cancelled</Badge>;
      default:
        return <Badge variant="neutral" size="sm">{status}</Badge>;
    }
  };

  return (
    <PageContainer>
      <PageHeader
        title="Purchasing & Supplier Orders"
        description="Track inward cloth roll consignments, mill purchases, and supplier shipments."
        breadcrumbs={[
          { label: 'Purchasing', href: '/purchases' },
          { label: 'Orders' },
        ]}
        actions={
          <Link to="/purchases/new">
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Plus className="w-4 h-4" />}
            >
              New Purchase
            </Button>
          </Link>
        }
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <div className="flex flex-col sm:flex-row items-center gap-3">
            <div className="flex-1 w-full">
              <Input
                placeholder="Search by order number or supplier..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                leftIcon={<Search className="w-4 h-4" />}
              />
            </div>
            <div className="w-full sm:w-44">
              <Select defaultValue="all">
                <option value="all">All Order Status</option>
                <option value="received">Received / Inwarded</option>
                <option value="pending">Pending Delivery</option>
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
              <TableHead>Order #</TableHead>
              <TableHead>Date</TableHead>
              <TableHead>Supplier Mill</TableHead>
              <TableHead align="right">Items Inward</TableHead>
              <TableHead align="right">Total Cost</TableHead>
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
                  <TableCell align="right"><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-16" /></TableCell>
                </TableRow>
              ))
            ) : filteredPurchases.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-8 text-zinc-500">
                  {searchTerm ? 'No matching purchases found' : 'No purchases recorded yet'}
                </TableCell>
              </TableRow>
            ) : (
              filteredPurchases.map((purchase) => (
                <TableRow key={purchase.id}>
                  <TableCell className="font-mono text-xs font-medium text-zinc-900">
                    {purchase.order_number || '—'}
                  </TableCell>
                  <TableCell className="text-xs text-zinc-600">
                    {formatDate(purchase.created_at, false)}
                  </TableCell>
                  <TableCell className="font-medium text-zinc-900">
                    {purchase.supplier_name || '—'}
                  </TableCell>
                  <TableCell align="right" className="font-tabular text-zinc-700">
                    {purchase.items_count} items
                  </TableCell>
                  <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
                    {formatCurrency(purchase.total_amount)}
                  </TableCell>
                  <TableCell>{getStatusBadge(purchase.status)}</TableCell>
                  <TableCell align="right">
                    <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                      Details
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