import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Search, BookOpen, Pencil, Trash2 } from 'lucide-react';
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
import { getCustomers, createCustomer, updateCustomer, deleteCustomer } from '../../lib/api/customers.ts';
import type { Customer } from '../../types/index.ts';

export function CustomersPage() {
  const [searchTerm, setSearchTerm] = useState('');
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [formData, setFormData] = useState({ name: '', phone: '', email: '', credit_limit: 0 });
  const [submitting, setSubmitting] = useState(false);
  const [editing, setEditing] = useState<Customer | null>(null);
  const [editData, setEditData] = useState({ name: '', phone: '', email: '', credit_limit: 0 });
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    const loadCustomers = async () => {
      try {
        setLoading(true);
        const data = await getCustomers();
        setCustomers(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load customers');
      } finally {
        setLoading(false);
      }
    };
    loadCustomers();
  }, []);

  const filteredCustomers = customers.filter(
    (customer) =>
      customer.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      customer.phone?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name) {
      setError('Customer name is required');
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      await createCustomer({
        name: formData.name,
        phone: formData.phone || undefined,
        email: formData.email || undefined,
        credit_limit: formData.credit_limit || undefined,
      });
      setIsAddModalOpen(false);
      setFormData({ name: '', phone: '', email: '', credit_limit: 0 });
      // Reload customers
      const data = await getCustomers();
      setCustomers(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create customer');
    } finally {
      setSubmitting(false);
    }
  };

  const getStatusBadge = (balance: number, creditLimit?: number | null) => {
    if (balance <= 0) {
      return <Badge variant="success" size="sm">Clear</Badge>;
    }
    if (creditLimit && balance >= creditLimit) {
      return <Badge variant="destructive" size="sm">Over Limit</Badge>;
    }
    return <Badge variant="warning" size="sm">Receivable</Badge>;
  };

  const reload = async () => {
    const data = await getCustomers();
    setCustomers(data);
  };

  const openEdit = (customer: Customer) => {
    setEditing(customer);
    setEditData({
      name: customer.name,
      phone: customer.phone ?? '',
      email: (customer as { email?: string | null }).email ?? '',
      credit_limit: customer.credit_limit ?? 0,
    });
    setError(null);
  };

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editing || !editData.name.trim()) {
      setError('Customer name is required');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await updateCustomer(editing.id, {
        name: editData.name.trim(),
        phone: editData.phone || null,
        email: editData.email || null,
        credit_limit: editData.credit_limit || null,
      });
      setEditing(null);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update customer');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (customer: Customer) => {
    if (!window.confirm(`Delete customer "${customer.name}"? Only customers with no sales or payments can be deleted.`)) {
      return;
    }
    setDeletingId(customer.id);
    setError(null);
    try {
      await deleteCustomer(customer.id);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete customer');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <PageContainer>
      <PageHeader
        title="Customer Directory & Khata"
        description="Maintain retail buyers, wholesale clients, contact numbers, and active balances."
        breadcrumbs={[
          { label: 'Relationships', href: '/customers' },
          { label: 'Customers' },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <Link to="/customers/khata">
              <Button variant="outline" size="sm" leftIcon={<BookOpen className="w-4 h-4" />}>
                Khata Ledger
              </Button>
            </Link>
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Plus className="w-4 h-4" />}
              onClick={() => setIsAddModalOpen(true)}
            >
              Add Customer
            </Button>
          </div>
        }
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <Input
            placeholder="Search customers by name or phone number..."
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
              <TableHead>Customer Name</TableHead>
              <TableHead>Phone Number</TableHead>
              <TableHead align="right">Credit Limit</TableHead>
              <TableHead align="right">Current Balance (Khata)</TableHead>
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
                  <TableCell align="right"><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-16" /></TableCell>
                </TableRow>
              ))
            ) : filteredCustomers.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center py-8 text-zinc-500">
                  {searchTerm ? 'No matching customers found' : 'No customers added yet'}
                </TableCell>
              </TableRow>
            ) : (
              filteredCustomers.map((customer) => (
                <TableRow key={customer.id}>
                  <TableCell className="font-medium text-zinc-900">
                    {customer.name}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-zinc-600">
                    {customer.phone || '—'}
                  </TableCell>
                  <TableCell align="right" className="font-tabular text-zinc-600">
                    {customer.credit_limit ? formatCurrency(customer.credit_limit) : '—'}
                  </TableCell>
                  <TableCell align="right" className="font-tabular font-bold text-rose-700">
                    {formatCurrency(customer.current_balance)}
                  </TableCell>
                  <TableCell>
                    {getStatusBadge(customer.current_balance, customer.credit_limit)}
                  </TableCell>
                  <TableCell align="right">
                    <div className="flex items-center justify-end gap-1">
                      <Link to={`/customers/khata/${customer.id}`}>
                        <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                          View Khata
                        </Button>
                      </Link>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 px-0"
                        title="Edit customer"
                        onClick={() => openEdit(customer)}
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 px-0 text-rose-600 hover:bg-rose-50"
                        title="Delete customer"
                        disabled={deletingId === customer.id}
                        onClick={() => handleDelete(customer)}
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

      {/* Add Customer Modal */}
      <Dialog
        open={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        title="Add New Customer"
        description="Record customer details for Khata and credit management."
        footer={
          <>
            <Button variant="outline" size="sm" onClick={() => setIsAddModalOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" onClick={handleSubmit} disabled={submitting}>
              {submitting ? 'Saving...' : 'Save Customer'}
            </Button>
          </>
        }
      >
        <form onSubmit={handleSubmit} className="space-y-3.5">
          <Input
            label="Customer Name *"
            placeholder="e.g. Chaudhry Fabric Traders"
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
          />
          <Input
            label="Phone Number"
            placeholder="0300-1234567"
            value={formData.phone}
            onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
          />
          <Input
            label="Email (Optional)"
            placeholder="customer@example.com"
            type="email"
            value={formData.email}
            onChange={(e) => setFormData({ ...formData, email: e.target.value })}
          />
          <Input
            label="Credit Limit (PKR) - Optional"
            placeholder="0.00"
            type="number"
            step="0.01"
            min="0"
            value={formData.credit_limit}
            onChange={(e) => setFormData({ ...formData, credit_limit: parseFloat(e.target.value) || 0 })}
          />
        </form>
      </Dialog>

      {/* Edit Customer Modal */}
      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title="Edit Customer"
        description="Update contact details or credit limit."
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
          <Input
            label="Customer Name *"
            value={editData.name}
            onChange={(e) => setEditData({ ...editData, name: e.target.value })}
          />
          <Input
            label="Phone Number"
            value={editData.phone}
            onChange={(e) => setEditData({ ...editData, phone: e.target.value })}
          />
          <Input
            label="Email (Optional)"
            type="email"
            value={editData.email}
            onChange={(e) => setEditData({ ...editData, email: e.target.value })}
          />
          <Input
            label="Credit Limit (PKR) - Optional"
            type="number"
            step="0.01"
            min="0"
            value={editData.credit_limit}
            onChange={(e) => setEditData({ ...editData, credit_limit: parseFloat(e.target.value) || 0 })}
          />
        </form>
      </Dialog>
    </PageContainer>
  );
}