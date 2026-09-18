import { useState, useEffect } from 'react';
import { ArrowLeft, Save, AlertCircle, Loader2, Trash2 } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
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
  CardHeader,
  CardTitle,
} from '../../components/ui/index.ts';
import { formatCurrency } from '../../lib/formatters.ts';
import { createPurchase, type CreatePurchaseRequest } from '../../lib/api/purchases.ts';
import { getProducts } from '../../lib/api/products.ts';
import { getSuppliers } from '../../lib/api/suppliers.ts';
import { useAuth } from '../../context/AuthContext.tsx';
import type { ProductVariant, Supplier } from '../../types/index.ts';

interface PurchaseItem {
  id: string;
  variant_id: string;
  product_name: string;
  sku: string;
  unit: string;
  quantity: number;
  unit_cost: number;
}

interface VariantWithProduct extends ProductVariant {
  product_name: string;
  unit: string;
}

export function NewPurchasePage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [supplierId, setSupplierId] = useState<string>('');
  const [invoiceNumber, setInvoiceNumber] = useState('');
  const [arrivalDate, setArrivalDate] = useState(() => new Date().toISOString().split('T')[0]);
  const [items, setItems] = useState<PurchaseItem[]>([]);
  const [variants, setVariants] = useState<VariantWithProduct[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const loadData = async () => {
      try {
        const [productsData, suppliersData] = await Promise.all([
          getProducts(),
          getSuppliers(),
        ]);
        const allVariants: VariantWithProduct[] = productsData.flatMap(product =>
          (product.variants || []).map(v => ({
            ...v,
            product_name: product.name,
            unit: product.unit,
          }))
        );
        setVariants(allVariants);
        setSuppliers(suppliersData);
      } catch (err) {
        console.error('Failed to load data:', err);
      }
    };
    loadData();
  }, []);

  const handleAddItem = (variant: VariantWithProduct) => {
    const existingIndex = items.findIndex(i => i.variant_id === variant.id);
    if (existingIndex >= 0) {
      const updated = [...items];
      updated[existingIndex] = {
        ...updated[existingIndex],
        quantity: updated[existingIndex].quantity + 1,
      };
      setItems(updated);
    } else {
      setItems([
        ...items,
        {
          id: crypto.randomUUID(),
          variant_id: variant.id,
          product_name: variant.product_name || 'Unknown Product',
          sku: variant.sku,
          unit: variant.unit || 'meters',
          quantity: 1,
          unit_cost: variant.cost_price,
        },
      ]);
    }
  };

  const handleUpdateQuantity = (itemId: string, quantity: number) => {
    if (quantity <= 0) {
      setItems(items.filter(i => i.id !== itemId));
    } else {
      setItems(items.map(i => (i.id === itemId ? { ...i, quantity } : i)));
    }
  };

  const handleUpdateCost = (itemId: string, unit_cost: number) => {
    setItems(items.map(i => (i.id === itemId ? { ...i, unit_cost } : i)));
  };

  const handleRemoveItem = (itemId: string) => {
    setItems(items.filter(i => i.id !== itemId));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (items.length === 0) {
      setError('Please add at least one item to the purchase');
      return;
    }
    if (!supplierId) {
      setError('Please select a supplier');
      return;
    }
    if (!user?.shopId) {
      setError('Shop not found');
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const payload: CreatePurchaseRequest = {
        supplier_id: supplierId,
        items: items.map(item => ({
          variant_id: item.variant_id,
          quantity: item.quantity,
          unit_cost: item.unit_cost,
        })),
        invoice_number: invoiceNumber || undefined,
      };

      await createPurchase(payload);
      navigate('/purchases');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create purchase';
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer>
      <form onSubmit={handleSubmit}>
        <div className="flex items-center gap-2 mb-2">
          <Link
            to="/purchases"
            className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-900 transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Back to Purchases
          </Link>
        </div>

        <PageHeader
          title="Record Inward Fabric Purchase"
          description="Receive rolls and consignment stock from mills and textile suppliers."
        />

        {error && (
          <div className="mb-4 p-3 bg-rose-50 border border-rose-200 rounded-md flex items-center gap-2 text-sm text-rose-700">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            {error}
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">
              Supplier & Consignment Details
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-zinc-700 mb-1">Supplier Mill</label>
                <Select value={supplierId} onChange={e => setSupplierId(e.target.value)}>
                  <option value="">Select Supplier</option>
                  {suppliers.map(s => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <label className="block text-xs font-medium text-zinc-700 mb-1">Purchase Order / Bilty #</label>
                <Input
                  placeholder="e.g. BL-89421"
                  value={invoiceNumber}
                  onChange={e => setInvoiceNumber(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-zinc-700 mb-1">Arrival Date</label>
                <Input
                  type="date"
                  value={arrivalDate}
                  onChange={e => setArrivalDate(e.target.value)}
                />
              </div>
            </div>

            <div className="border-t border-zinc-100 pt-4 space-y-3">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-semibold uppercase text-zinc-500 tracking-wider">
                  Item Details
                </h4>
                <div className="flex gap-2">
                  {variants.filter(p => p.is_active).map(variant => (
                    <button
                      key={variant.id}
                      type="button"
                      onClick={() => handleAddItem(variant)}
                      className="px-3 py-1.5 text-xs bg-zinc-100 hover:bg-zinc-200 rounded-md border border-zinc-200 transition-colors"
                    >
                      + {variant.sku}
                    </button>
                  ))}
                </div>
              </div>

              {items.length === 0 ? (
                <p className="text-center text-zinc-500 py-8">No items added yet. Click a product button above to add.</p>
              ) : (
                <div className="space-y-3">
                  {items.map((item) => (
                    <div key={item.id} className="grid grid-cols-1 sm:grid-cols-4 gap-3 p-3 bg-zinc-50 rounded-md border border-zinc-100">
                      <div className="sm:col-span-2">
                        <label className="block text-xs font-medium text-zinc-700 mb-1">Fabric Product</label>
                        <div className="font-medium text-zinc-900">{item.product_name}</div>
                        <div className="text-xs text-zinc-500 font-mono">{item.sku}</div>
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-zinc-700 mb-1">Quantity (Meters)</label>
                        <Input
                          type="number"
                          step="0.01"
                          min="0.01"
                          value={item.quantity}
                          onChange={e => handleUpdateQuantity(item.id, parseFloat(e.target.value) || 0)}
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-zinc-700 mb-1">Unit Cost (PKR)</label>
                        <Input
                          type="number"
                          step="1"
                          min="0"
                          value={item.unit_cost}
                          onChange={e => handleUpdateCost(item.id, parseFloat(e.target.value) || 0)}
                        />
                      </div>
                      <div className="flex items-end">
                        <div className="w-full text-right">
                          <div className="font-semibold text-zinc-900 font-tabular">
                            {formatCurrency(item.quantity * item.unit_cost)}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleRemoveItem(item.id)}
                          className="text-zinc-400 hover:text-rose-600 p-1 rounded ml-2"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="border-t border-zinc-100 pt-4 flex justify-end gap-2">
              <Link to="/purchases">
                <Button type="button" variant="outline" size="sm">
                  Cancel
                </Button>
              </Link>
              <Button
                type="submit"
                variant="primary"
                size="sm"
                disabled={submitting || items.length === 0}
                leftIcon={submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              >
                {submitting ? 'Recording...' : 'Record Consignment'}
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>
    </PageContainer>
  );
}