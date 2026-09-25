import { useState, useEffect } from 'react';
import { Plus, Edit, Trash2 } from 'lucide-react';
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
import { getCategories, createCategory, updateCategory, deleteCategory } from '../../lib/api/products.ts';
import { ApiError } from '../../lib/api/client.ts';
import type { Category } from '../../types/index.ts';

export function CategoriesPage() {
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formData, setFormData] = useState({ name: '', code: '', parent_id: '' });
  const [submitting, setSubmitting] = useState(false);
  const [editing, setEditing] = useState<Category | null>(null);
  const [editName, setEditName] = useState('');
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const reload = async () => {
    const data = await getCategories();
    setCategories(data);
  };

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

  const openEdit = (category: Category) => {
    setEditing(category);
    setEditName(category.name);
    setError(null);
  };

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editing || !editName.trim()) {
      setError('Category name is required');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await updateCategory(editing.id, { name: editName.trim() });
      setEditing(null);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update category');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (category: Category) => {
    if (!window.confirm(`Delete category "${category.name}"? Categories with sub-categories or products cannot be deleted.`)) {
      return;
    }
    setDeletingId(category.id);
    setError(null);
    try {
      await deleteCategory(category.id);
      await reload();
    } catch (err) {
      // A category holding products blocks a plain delete - offer to
      // permanently delete the products (and their stock history) with it.
      if (
        err instanceof ApiError &&
        err.status === 409 &&
        /product/i.test(err.message) &&
        window.confirm(
          `${err.message}\n\n` +
          `Permanently delete category "${category.name}" INCLUDING its products ` +
          `and their stock movement history? This cannot be undone.`
        )
      ) {
        try {
          await deleteCategory(category.id, true);
          await reload();
          return;
        } catch (retryErr) {
          setError(retryErr instanceof Error ? retryErr.message : 'Failed to delete category');
        }
      } else {
        setError(err instanceof Error ? err.message : 'Failed to delete category');
      }
    } finally {
      setDeletingId(null);
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
                      {(category.products_count ?? category.variants_count ?? 0) > 0
                        ? `${category.products_count ?? category.variants_count} products`
                        : '0 products'}
                    </Badge>
                  </TableCell>
                  <TableCell align="right">
                    <div className="flex items-center justify-end gap-1">
                      <Button variant="ghost" size="sm" className="h-7 w-7 px-0" title="Edit category" onClick={() => openEdit(category)}>
                        <Edit className="w-3.5 h-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 px-0 text-rose-600 hover:bg-rose-50"
                        title="Delete category"
                        disabled={deletingId === category.id}
                        onClick={() => handleDelete(category)}
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

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title="Edit Category"
        description="Rename the category."
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
            label="Category Name *"
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
          />
        </form>
      </Dialog>
    </PageContainer>
  );
}