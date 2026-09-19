import { useState, useEffect } from 'react';
import { Plus, Search, Edit } from 'lucide-react';
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
import { formatCurrency } from '../../lib/formatters.ts';
import { getProducts, createProduct, getCategories } from '../../lib/api/products.ts';
import type { Product, Category } from '../../types/index.ts';

export function ProductsPage() {
  const [searchTerm, setSearchTerm] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [unitFilter, setUnitFilter] = useState('all');
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Form state for add product
  const [formData, setFormData] = useState({
    name: '',
    code: '',
    category_id: '',
    unit: 'meters' as const,
    description: '',
    variants: [{ sku: '', barcode: '', cost_price: 0, selling_price: 0, attributes: {} }],
  });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true);
        const [productsData, categoriesData] = await Promise.all([
          getProducts(),
          getCategories(),
        ]);
        setProducts(productsData);
        setCategories(categoriesData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load products');
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, []);

  const filteredProducts = products.filter((product) => {
    const matchesSearch =
      product.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      product.code?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      product.variants?.some((v: { sku: string }) => v.sku.toLowerCase().includes(searchTerm.toLowerCase()));
    const matchesCategory = categoryFilter === 'all' || product.category_id === categoryFilter;
    const matchesUnit = unitFilter === 'all' || product.unit === unitFilter;
    return matchesSearch && matchesCategory && matchesUnit;
  });

  const handleAddVariant = () => {
    setFormData((prev) => ({
      ...prev,
      variants: [...prev.variants, { sku: '', barcode: '', cost_price: 0, selling_price: 0, attributes: {} }],
    }));
  };

  const handleRemoveVariant = (index: number) => {
    setFormData((prev) => ({
      ...prev,
      variants: prev.variants.filter((_, i) => i !== index),
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name.trim()) {
      setError('Product name is required');
      return;
    }

    const preparedVariants = formData.variants.map((v, i) => ({
      ...v,
      sku: v.sku.trim() || (i === 0 && formData.code ? formData.code.trim() : `${formData.name.substring(0, 3).toUpperCase()}-${String(i + 1).padStart(2, '0')}`),
    }));

    setSubmitting(true);
    setError(null);

    try {
      await createProduct({
        name: formData.name,
        code: formData.code || undefined,
        category_id: formData.category_id || undefined,
        unit: formData.unit,
        description: formData.description || undefined,
        variants: preparedVariants.map((v) => ({
          sku: v.sku,
          barcode: v.barcode || undefined,
          purchase_price: v.cost_price,
          selling_price: v.selling_price,
          unit: formData.unit,
          attributes: v.attributes,
        })),
      });
      setIsAddModalOpen(false);
      setFormData({
        name: '',
        code: '',
        category_id: '',
        unit: 'meters',
        description: '',
        variants: [{ sku: '', barcode: '', cost_price: 0, selling_price: 0, attributes: {} }],
      });
      // Reload products
      const data = await getProducts();
      setProducts(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create product');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer>
      <PageHeader
        title="Products & Fabric Catalog"
        description="Manage fabrics, materials, suitings, unstitched collections, and apparel variants."
        breadcrumbs={[
          { label: 'Catalog', href: '/products' },
          { label: 'Products' },
        ]}
        actions={
          <Button
            variant="primary"
            size="sm"
            leftIcon={<Plus className="w-4 h-4" />}
            onClick={() => setIsAddModalOpen(true)}
          >
            Add Product
          </Button>
        }
      />

      {/* Filter and Search Bar */}
      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <div className="flex flex-col sm:flex-row items-center gap-3">
            <div className="flex-1 w-full">
              <Input
                placeholder="Search products by title, SKU, fabric type..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                leftIcon={<Search className="w-4 h-4" />}
              />
            </div>
            <div className="w-full sm:w-48">
              <Select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
                <option value="all">All Categories</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </div>
            <div className="w-full sm:w-36">
              <Select value={unitFilter} onChange={(e) => setUnitFilter(e.target.value)}>
                <option value="all">All Units</option>
                <option value="meters">Meters</option>
                <option value="yards">Yards</option>
                <option value="pieces">Pieces</option>
                <option value="rolls">Rolls</option>
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

      {/* Products Table Shell */}
      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Code / SKU</TableHead>
              <TableHead>Product Name</TableHead>
              <TableHead>Category</TableHead>
              <TableHead>Base Unit</TableHead>
              <TableHead align="right">Cost Price</TableHead>
              <TableHead align="right">Selling Price</TableHead>
              <TableHead>Status</TableHead>
              <TableHead align="right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-48" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-16" /></TableCell>
                </TableRow>
              ))
            ) : filteredProducts.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center py-8 text-zinc-500">
                  {searchTerm ? 'No matching products found' : 'No products added yet'}
                </TableCell>
              </TableRow>
            ) : (
              filteredProducts.map((product) => (
                <TableRow key={product.id}>
                  <TableCell className="font-mono text-xs text-zinc-500">
                    {product.code || product.variants?.[0]?.sku || '—'}
                  </TableCell>
                  <TableCell className="font-medium text-zinc-900">
                    {product.name}
                  </TableCell>
                  <TableCell>
                    <Badge variant="neutral" size="sm">
                      {product.category?.name || 'Uncategorized'}
                    </Badge>
                  </TableCell>
                  <TableCell className="capitalize text-zinc-600">{product.unit}</TableCell>
                  <TableCell align="right" className="font-tabular text-zinc-600">
                    {product.variants?.[0] ? formatCurrency(product.variants[0].purchase_price) : '—'}
                  </TableCell>
                  <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
                    {product.variants?.[0] ? formatCurrency(product.variants[0].selling_price) : '—'}
                  </TableCell>
                  <TableCell>
                    <Badge variant="success" size="sm">Active</Badge>
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

      {/* Add Product Modal Shell */}
      <Dialog
        open={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        title="Add New Fabric Product"
        description="Define standard catalog metadata and measurement unit."
        footer={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsAddModalOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleSubmit}
              disabled={submitting}
            >
              {submitting ? 'Saving...' : 'Save Product'}
            </Button>
          </>
        }
      >
        <form onSubmit={handleSubmit}>
          <div className="space-y-3.5">
            <Input
              label="Product Title"
              placeholder="e.g. Wash & Wear Soft Finish"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            />
            <div className="grid grid-cols-2 gap-3">
              <Input
                label="Product Code / SKU"
                placeholder="e.g. WNW-01"
                value={formData.code}
                onChange={(e) => {
                  const val = e.target.value;
                  setFormData((prev) => {
                    const updatedVariants = [...prev.variants];
                    if (updatedVariants.length > 0 && (!updatedVariants[0].sku || updatedVariants[0].sku === prev.code)) {
                      updatedVariants[0] = { ...updatedVariants[0], sku: val };
                    }
                    return { ...prev, code: val, variants: updatedVariants };
                  });
                }}
              />
              <Select
                label="Measurement Unit"
                value={formData.unit}
                onChange={(e) => setFormData({ ...formData, unit: e.target.value as any })}
              >
                <option value="meters">Meters</option>
                <option value="yards">Yards</option>
                <option value="pieces">Pieces</option>
                <option value="sets">Sets</option>
                <option value="rolls">Rolls</option>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Select
                label="Category"
                value={formData.category_id}
                onChange={(e) => setFormData({ ...formData, category_id: e.target.value })}
              >
                <option value="">Select Category</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Input
                label="Default Cost Price (PKR)"
                placeholder="0.00"
                type="number"
                step="0.01"
                value={formData.variants[0]?.cost_price || ''}
                onChange={(e) => {
                  const cost = parseFloat(e.target.value) || 0;
                  setFormData((prev) => {
                    const updatedVariants = [...prev.variants];
                    if (updatedVariants.length > 0) {
                      updatedVariants[0] = { ...updatedVariants[0], cost_price: cost };
                    }
                    return { ...prev, variants: updatedVariants };
                  });
                }}
              />
              <Input
                label="Default Selling Price (PKR)"
                placeholder="0.00"
                type="number"
                step="0.01"
                value={formData.variants[0]?.selling_price || ''}
                onChange={(e) => {
                  const price = parseFloat(e.target.value) || 0;
                  setFormData((prev) => {
                    const updatedVariants = [...prev.variants];
                    if (updatedVariants.length > 0) {
                      updatedVariants[0] = { ...updatedVariants[0], selling_price: price };
                    }
                    return { ...prev, variants: updatedVariants };
                  });
                }}
              />
            </div>
            <Input
              label="Description (Optional)"
              placeholder="Additional product details..."
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            />
            <div className="border-t border-zinc-100 pt-3">
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-sm font-medium text-zinc-900">Variants</h4>
                <Button type="button" variant="outline" size="sm" onClick={handleAddVariant}>
                  <Plus className="w-3.5 h-3.5 mr-1" />
                  Add Variant
                </Button>
              </div>
              <div className="space-y-2">
                {formData.variants.map((variant, index) => (
                  <div key={index} className="grid grid-cols-2 md:grid-cols-5 gap-3 p-3 bg-zinc-50 rounded-md border border-zinc-100">
                    <Input
                      label="Variant SKU *"
                      placeholder="e.g. WNW-01-BLK"
                      value={variant.sku}
                      onChange={(e) => {
                        const updated = [...formData.variants];
                        updated[index] = { ...updated[index], sku: e.target.value };
                        setFormData({ ...formData, variants: updated });
                      }}
                    />
                    <Input
                      label="Barcode (Optional)"
                      placeholder="EAN/UPC"
                      value={variant.barcode}
                      onChange={(e) => {
                        const updated = [...formData.variants];
                        updated[index] = { ...updated[index], barcode: e.target.value };
                        setFormData({ ...formData, variants: updated });
                      }}
                    />
                    <Input
                      label="Cost Price (PKR)"
                      type="number"
                      step="0.01"
                      min="0"
                      value={variant.cost_price}
                      onChange={(e) => {
                        const updated = [...formData.variants];
                        updated[index] = { ...updated[index], cost_price: parseFloat(e.target.value) || 0 };
                        setFormData({ ...formData, variants: updated });
                      }}
                    />
                    <Input
                      label="Selling Price (PKR)"
                      type="number"
                      step="0.01"
                      min="0"
                      value={variant.selling_price}
                      onChange={(e) => {
                        const updated = [...formData.variants];
                        updated[index] = { ...updated[index], selling_price: parseFloat(e.target.value) || 0 };
                        setFormData({ ...formData, variants: updated });
                      }}
                    />
                    <div className="flex items-end">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => handleRemoveVariant(index)}
                        disabled={formData.variants.length <= 1}
                        className="text-rose-600 hover:bg-rose-50"
                      >
                        Remove
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </form>
      </Dialog>
    </PageContainer>
  );
}