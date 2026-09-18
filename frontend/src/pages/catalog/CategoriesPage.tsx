import { useState, useEffect } from 'react';
import { Plus, Edit } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Button,
  Input,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Badge,
  Dialog,
  Skeleton,
  Select,
  Card,
} from '../../components/ui/index.ts';
import { getCategories, createCategory } from '../../lib/api/products.ts';
import type { Category } from '../../types/index.ts';

export function CategoriesPage() {
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formData, setFormData] = useState({ name: '', code: '', parent_id: '' });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const loadCategories = async () => {
      try {
        setLoading(true);
        const data = await getCategories();
        setCategories(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load categories');
      } finally {
        setLoading(false);
      }
    };
    loadCategories();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name.trim()) {
      setError('Category name is required');
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      await createCategory({
        name: formData.name,
        code: formData.code || undefined,
        parent_id: formData.parent_id || undefined,
      });
      setIsAddModalOpen(false);
      setFormData({ name: '', code: '', parent_id: '' });
      // Reload categories
      const data = await getCategories();
      setCategories(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create category');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer>
      <PageHeader
        title="Product Categories"
        description="Organize fabrics, apparel, and stock items into business classifications."
        breadcrumbs={[
          { label: 'Catalog', href: '/products' },
          { label: 'Categories' },
        ]}
        actions={
          <Button
            variant="primary"
            size="sm"
            leftIcon={<Plus className="w-4 h-4" />}
            onClick={() => setIsAddModalOpen(true)}
          >
            Add Category
          </Button>
        }
      />

      {error && (
        <div className="mb-4 p-3 bg-rose-50 border border-rose-200 rounded-md text-sm text-rose-700">
          {error}
        </div>
      )}

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Category Name</TableHead>
              <TableHead>Code</TableHead>
              <TableHead>Parent Category</TableHead>
              <TableHead align="center">Products Count</TableHead>
              <TableHead align="right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell align="center"><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-16" /></TableCell>
                </TableRow>
              ))
            ) : categories.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center py-8 text-zinc-500">
                  No categories created yet
                </TableCell>
              </TableRow>
            ) : (
              categories.map((category) => (
                <TableRow key={category.id}>
                  <TableCell className="font-medium text-zinc-900">
                    {category.name}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-zinc-500">
                    {category.code || '—'}
                  </TableCell>
                  <TableCell className="text-zinc-500">
                    {categories.find((c) => c.id === category.parent_id)?.name || '—'}
                  </TableCell>
                  <TableCell align="center">
                    <Badge variant="neutral" size="sm">
                      {category.variants_count ? `${category.variants_count} products` : '0 products'}
                    </Badge>
                  </TableCell>
                  <TableCell align="right">
                    <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                      <Edit className="w-3.5 h-3.5" />
                    </Button>
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
        title="Add New Category"
        description="Create a category to group fabric items."
        footer={
          <Button variant="outline" size="sm" onClick={() => setIsAddModalOpen(false)}>
            Cancel
          </Button>
        }
      >
        <form onSubmit={handleSubmit} className="space-y-3.5">
          <Input
            label="Category Name"
            placeholder="e.g. Winter Shawls & Wool"
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
          />
          <Input
            label="Category Code (Optional)"
            placeholder="e.g. CAT-SHAWLS"
            value={formData.code}
            onChange={(e) => setFormData({ ...formData, code: e.target.value })}
          />
          <Select
            label="Parent Category (Optional)"
            value={formData.parent_id}
            onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setFormData({ ...formData, parent_id: e.target.value })}
          >
            <option value="">None (Top Level)</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
          <div className="flex justify-end gap-2 mt-4 border-t border-zinc-100 pt-4">
            <Button variant="primary" size="sm" type="submit" disabled={submitting}>
              {submitting ? 'Saving...' : 'Save Category'}
            </Button>
          </div>
        </form>
      </Dialog>
    </PageContainer>
  );
}