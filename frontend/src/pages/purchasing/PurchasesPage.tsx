import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Search, Pencil, Trash2 } from 'lucide-react';
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
  Dialog,
} from '../../components/ui/index.ts';
import { formatCurrency, formatDate } from '../../lib/formatters.ts';
import { getPurchases, updatePurchase, deletePurchase } from '../../lib/api/purchases.ts';
import { getSuppliers } from '../../lib/api/suppliers.ts';
import type { Purchase, Supplier } from '../../types/index.ts';

export function PurchasesPage() {
  const [searchTerm, setSearchTerm] = useState('');
  const [purchases, setPurchases] = useState<Purchase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [editing, setEditing] = useState<Purchase | null>(null);
  const [editData, setEditData] = useState({ supplier_id: '', invoice_number: '', discount: 0 });
  const [submitting, setSubmitting] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const reload = async () => {
    const data = await getPurchases();
    setPurchases(data);
  };

  useEffect(() => {
    const loadPurchases = async () => {
      try {
        setLoading(true);
        const [purchasesData, suppliersData] = await Promise.all([
          getPurchases(),
          getSuppliers().catch(() => [] as Supplier[]),
        ]);
        setPurchases(purchasesData);
        setSuppliers(suppliersData);
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

  const openEdit = (purchase: Purchase) => {
    setEditing(purchase);
    setEditData({
      supplier_id: purchase.supplier_id ?? '',
      invoice_number: purchase.order_number ?? '',
      discount: 0,
    });
    setError(null);
  };

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editing) return;
    setSubmitting(true);
    setError(null);
    try {
      await updatePurchase(editing.id, {
        supplier_id: editData.supplier_id || undefined,
        invoice_number: editData.invoice_number || null,
        discount: editData.discount || 0,
      });
      setEditing(null);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update purchase');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (purchase: Purchase) => {
    if (!window.confirm(
      `Void purchase ${purchase.order_number || purchase.id}? Stock will be pulled back out and its ledger posting removed. ` +
      `Fails if the goods were already sold. This cannot be undone.`
    )) {
      return;
    }
    setDeletingId(purchase.id);
    setError(null);
    try {
      await deletePurchase(purchase.id);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete purchase');
    } finally {
      setDeletingId(null);
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
                    <div className="flex items-center justify-end gap-1">
                      <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                        Details
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 px-0"
                        title="Edit purchase (supplier / invoice / discount)"
                        onClick={() => openEdit(purchase)}
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 px-0 text-rose-600 hover:bg-rose-50"
                        title="Void purchase"
                        disabled={deletingId === purchase.id}
                        onClick={() => handleDelete(purchase)}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title="Edit Purchase"
        description="Fix supplier, invoice number or discount. Stock and ledger are reposted automatically. To change items or quantities, void and re-enter the purchase."
        footer={
          <>
            <Button variant="outline" size="sm" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" onClick={handleEditSubmit} disabled={submitting}>
              {submitting ? 'Saving...' : 'Save Changes'}
            </Button>
          </>
        }
      >
        <form onSubmit={handleEditSubmit} className="space-y-3.5">
          <Select
            label="Supplier"
            value={editData.supplier_id}
            onChange={(e) => setEditData({ ...editData, supplier_id: e.target.value })}
          >
            <option value="">Keep current supplier</option>
            {suppliers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
          <Input
            label="Invoice Number"
            value={editData.invoice_number}
            onChange={(e) => setEditData({ ...editData, invoice_number: e.target.value })}
          />
          <Input
            label="Discount (PKR)"
            type="number"
            step="0.01"
            min="0"
            value={editData.discount}
            onChange={(e) => setEditData({ ...editData, discount: parseFloat(e.target.value) || 0 })}
          />
        </form>
      </Dialog>
    </PageContainer>
  );
}