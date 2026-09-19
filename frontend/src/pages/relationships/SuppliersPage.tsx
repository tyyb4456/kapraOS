import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Search, Building2 } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Button,
  Input,
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
import { formatCurrency } from '../../lib/formatters.ts';
import { getSuppliers, createSupplier } from '../../lib/api/suppliers.ts';
import type { Supplier } from '../../types/index.ts';

export function SuppliersPage() {
  const [searchTerm, setSearchTerm] = useState('');
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [formData, setFormData] = useState({ name: '', phone: '', address: '', notes: '' });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const loadSuppliers = async () => {
      try {
        setLoading(true);
        const data = await getSuppliers();
        setSuppliers(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load suppliers');
      } finally {
        setLoading(false);
      }
    };
    loadSuppliers();
  }, []);

  const filteredSuppliers = suppliers.filter(
    (supplier) =>
      supplier.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name) {
      setError('Supplier name is required');
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      await createSupplier({
        name: formData.name,
        phone: formData.phone || undefined,
        address: formData.address || undefined,
        notes: formData.notes || undefined,
      });
      setIsAddModalOpen(false);
      setFormData({ name: '', phone: '', address: '', notes: '' });
      // Reload suppliers
      const data = await getSuppliers();
      setSuppliers(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create supplier');
    } finally {
      setSubmitting(false);
    }
  };

  const getStatusBadge = (balance: number) => {
    if (balance <= 0) {
      return <Badge variant="success" size="sm">Clear</Badge>;
    }
    return <Badge variant="neutral" size="sm">Active Supplier</Badge>;
  };

  return (
    <PageContainer>
      <PageHeader
        title="Suppliers & Textile Mills"
        description="Maintain fabric suppliers, weaving mills, wholesale contacts, and payables."
        breadcrumbs={[
          { label: 'Relationships', href: '/suppliers' },
          { label: 'Suppliers' },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <Link to="/suppliers/khata">
              <Button variant="outline" size="sm" leftIcon={<Building2 className="w-4 h-4" />}>
                Supplier Khata
              </Button>
            </Link>
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Plus className="w-4 h-4" />}
              onClick={() => setIsAddModalOpen(true)}
            >
              Add Supplier
            </Button>
          </div>
        }
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <Input
            placeholder="Search suppliers by name..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            leftIcon={<Search className="w-4 h-4" />}
          />
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
              <TableHead>Supplier / Mill Name</TableHead>
              <TableHead>Phone</TableHead>
              <TableHead>Address</TableHead>
              <TableHead align="right">Current Payable</TableHead>
              <TableHead>Status</TableHead>
              <TableHead align="right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton className="h-4 w-40" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-16" /></TableCell>
                </TableRow>
              ))
            ) : filteredSuppliers.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center py-8 text-zinc-500">
                  {searchTerm ? 'No matching suppliers found' : 'No suppliers added yet'}
                </TableCell>
              </TableRow>
            ) : (
              filteredSuppliers.map((supplier) => (
                <TableRow key={supplier.id}>
                  <TableCell className="font-medium text-zinc-900">
                    {supplier.name}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-zinc-600">
                    {supplier.phone || '—'}
                  </TableCell>
                  <TableCell className="text-zinc-700 text-sm max-w-[200px] truncate">
                    {supplier.address || '—'}
                  </TableCell>
                  <TableCell align="right" className="font-tabular font-bold text-zinc-900">
                    {formatCurrency(supplier.current_balance)}
                  </TableCell>
                  <TableCell>
                    {getStatusBadge(supplier.current_balance)}
                  </TableCell>
                  <TableCell align="right">
                    <Link to={`/suppliers/khata/${supplier.id}`}>
                      <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                        View Ledger
                      </Button>
                    </Link>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      {/* Add Supplier Modal */}
      <Dialog
        open={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        title="Add New Supplier"
        description="Record textile mill or fabric supplier details for purchasing and payables."
        footer={
          <>
            <Button variant="outline" size="sm" onClick={() => setIsAddModalOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" onClick={handleSubmit} disabled={submitting}>
              {submitting ? 'Saving...' : 'Save Supplier'}
            </Button>
          </>
        }
      >
        <form onSubmit={handleSubmit} className="space-y-3.5">
          <Input
            label="Supplier / Mill Name *"
            placeholder="e.g. Kohinoor Textile Mills Ltd."
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
          />
          <Input
            label="Phone Number"
            placeholder="042-3591234"
            value={formData.phone}
            onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
          />
          <Input
            label="Address (Optional)"
            placeholder="123 Industrial Area, Lahore"
            value={formData.address}
            onChange={(e) => setFormData({ ...formData, address: e.target.value })}
          />
          <Input
            label="Notes (Optional)"
            placeholder="Wholesale fabric supplier, payment terms: Net 30"
            value={formData.notes}
            onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
          />
        </form>
      </Dialog>
    </PageContainer>
  );
}