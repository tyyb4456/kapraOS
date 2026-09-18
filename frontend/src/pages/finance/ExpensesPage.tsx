import { useState, useEffect } from 'react';
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
  Dialog,
  Skeleton,
} from '../../components/ui/index.ts';
import { formatCurrency, formatDate } from '../../lib/formatters.ts';
import { getExpenses, createExpense } from '../../lib/api/expenses.ts';
import type { Expense } from '../../types/index.ts';

export function ExpensesPage() {
  const [searchTerm, setSearchTerm] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [expenses, setExpenses] = useState<Expense[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [formData, setFormData] = useState({
    category: 'utilities',
    amount: 0,
    description: '',
    payment_method: 'cash' as 'cash' | 'bank',
    date: new Date().toISOString().split('T')[0],
    receipt_ref: '',
  });
  const [submitting, setSubmitting] = useState(false);

  const categories = [
    { value: 'utilities', label: 'Electricity & Utilities' },
    { value: 'refreshment', label: 'Tea / Staff Meals' },
    { value: 'packaging', label: 'Bags & Packaging' },
    { value: 'rent', label: 'Shop Rent' },
    { value: 'transport', label: 'Freight / Transport' },
    { value: 'other', label: 'Other' },
  ];

  useEffect(() => {
    const loadExpenses = async () => {
      try {
        setLoading(true);
        const data = await getExpenses();
        setExpenses(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load expenses');
      } finally {
        setLoading(false);
      }
    };
    loadExpenses();
  }, []);

  const filteredExpenses = expenses.filter((expense) => {
    const matchesSearch =
      expense.description.toLowerCase().includes(searchTerm.toLowerCase()) ||
      expense.receipt_ref?.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesCategory = categoryFilter === 'all' || expense.category === categoryFilter;
    return matchesSearch && matchesCategory;
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.amount || !formData.description) {
      setError('Amount and description are required');
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      await createExpense({
        category: formData.category,
        amount: formData.amount,
        description: formData.description,
        payment_method: formData.payment_method,
        date: formData.date,
        receipt_ref: formData.receipt_ref || undefined,
      });
      setIsAddModalOpen(false);
      setFormData({
        category: 'utilities',
        amount: 0,
        description: '',
        payment_method: 'cash',
        date: new Date().toISOString().split('T')[0],
        receipt_ref: '',
      });
      // Reload expenses
      const data = await getExpenses();
      setExpenses(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create expense');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer>
      <PageHeader
        title="Shop Operating Expenses"
        description="Log everyday store overheads: shop rent, electricity, staff chai/meals, packaging, and logistics."
        breadcrumbs={[
          { label: 'Finance', href: '/expenses' },
          { label: 'Expenses' },
        ]}
        actions={
          <Button
            variant="primary"
            size="sm"
            leftIcon={<Plus className="w-4 h-4" />}
            onClick={() => setIsAddModalOpen(true)}
          >
            Record Expense
          </Button>
        }
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <div className="flex flex-col sm:flex-row items-center gap-3">
            <div className="flex-1 w-full">
              <Input
                placeholder="Search by description or receipt reference..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                leftIcon={<Search className="w-4 h-4" />}
              />
            </div>
            <div className="w-full sm:w-48">
              <Select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
                <option value="all">All Categories</option>
                {categories.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
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
              <TableHead>Date</TableHead>
              <TableHead>Expense Category</TableHead>
              <TableHead>Description</TableHead>
              <TableHead>Payment Method</TableHead>
              <TableHead align="right">Amount</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-48" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-20" /></TableCell>
                </TableRow>
              ))
            ) : filteredExpenses.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center py-8 text-zinc-500">
                  {searchTerm ? 'No matching expenses found' : 'No expenses recorded yet'}
                </TableCell>
              </TableRow>
            ) : (
              filteredExpenses.map((expense) => (
                <TableRow key={expense.id}>
                  <TableCell className="text-xs text-zinc-600">
                    {formatDate(expense.date, true)}
                  </TableCell>
                  <TableCell>
                    <Badge variant="neutral" size="sm">
                      {categories.find((c) => c.value === expense.category)?.label || expense.category}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-medium text-zinc-900">
                    {expense.description}
                  </TableCell>
                  <TableCell className="capitalize text-zinc-600">
                    {expense.payment_method === 'cash' ? 'Cash' : 'Bank'}
                  </TableCell>
                  <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
                    {formatCurrency(expense.amount)}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      <Dialog
        open={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        title="Record Store Expense"
        description="Log operational costs to accurately reflect net profit."
        footer={
          <>
            <Button variant="outline" size="sm" onClick={() => setIsAddModalOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" onClick={handleSubmit} disabled={submitting}>
              {submitting ? 'Saving...' : 'Save Expense'}
            </Button>
          </>
        }
      >
        <form onSubmit={handleSubmit} className="space-y-3.5">
          <Select
            label="Expense Category"
            value={formData.category}
            onChange={(e) => setFormData({ ...formData, category: e.target.value })}
          >
            {categories.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </Select>
          <Input
            label="Amount (PKR) *"
            placeholder="0.00"
            type="number"
            step="0.01"
            min="0"
            value={formData.amount}
            onChange={(e) => setFormData({ ...formData, amount: parseFloat(e.target.value) || 0 })}
          />
          <Input
            label="Description *"
            placeholder="e.g. Monthly electricity bill for shop #4"
            value={formData.description}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
          />
          <Input
            label="Date *"
            type="date"
            value={formData.date}
            onChange={(e) => setFormData({ ...formData, date: e.target.value })}
          />
          <Input
            label="Receipt Reference (Optional)"
            placeholder="e.g. INV-2026-001"
            value={formData.receipt_ref}
            onChange={(e) => setFormData({ ...formData, receipt_ref: e.target.value })}
          />
          <Select
            label="Payment Source"
            value={formData.payment_method}
            onChange={(e) => setFormData({ ...formData, payment_method: e.target.value as 'cash' | 'bank' })}
          >
            <option value="cash">Shop Cash Drawer</option>
            <option value="bank">Bank Account</option>
          </Select>
        </form>
      </Dialog>
    </PageContainer>
  );
}