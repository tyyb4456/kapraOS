import { useState, useEffect } from 'react';
import { Trash2, CheckCircle2, ArrowLeft, Plus, AlertCircle, Loader2, Search } from 'lucide-react';
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
  Badge,
} from '../../components/ui/index.ts';
import { formatCurrency } from '../../lib/formatters.ts';
import { createSale, type CreateSaleRequest } from '../../lib/api/sales.ts';
import { getProducts } from '../../lib/api/products.ts';
import { useAuth } from '../../context/AuthContext.tsx';
import type { ProductVariant } from '../../types/index.ts';

interface SaleItem {
  id: string;
  variant_id: string;
  product_name: string;
  sku: string;
  unit: string;
  quantity: number;
  unit_price: number;
  discount: number;
}

interface VariantWithProduct extends ProductVariant {
  product_name: string;
  unit: string;
}

export function NewSalePage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [paymentMethod, setPaymentMethod] = useState<'cash' | 'khata'>('cash');
  const [customerId, setCustomerId] = useState<string>('walk_in');
  const [customerPhone, setCustomerPhone] = useState('');
  const [items, setItems] = useState<SaleItem[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [showProductPicker, setShowProductPicker] = useState(false);
  const [variants, setVariants] = useState<VariantWithProduct[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const loadProducts = async () => {
      try {
        const data = await getProducts();
        const allVariants: VariantWithProduct[] = data.flatMap(product =>
          (product.variants || []).map(v => ({
            ...v,
            product_name: product.name,
            unit: product.unit,
          }))
        );
        setVariants(allVariants);
      } catch (err) {
        console.error('Failed to load products:', err);
      }
    };
    loadProducts();
  }, []);

  const subtotal = items.reduce((sum, item) => sum + item.quantity * item.unit_price, 0);
  const totalDiscount = items.reduce((sum, item) => sum + item.discount, 0);
  const netTotal = subtotal - totalDiscount;

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
          unit_price: variant.selling_price,
          discount: 0,
        },
      ]);
    }
    setShowProductPicker(false);
    setSearchQuery('');
  };

  const handleUpdateQuantity = (itemId: string, quantity: number) => {
    if (quantity <= 0) {
      setItems(items.filter(i => i.id !== itemId));
    } else {
      setItems(items.map(i => (i.id === itemId ? { ...i, quantity } : i)));
    }
  };

  const handleUpdatePrice = (itemId: string, unit_price: number) => {
    setItems(items.map(i => (i.id === itemId ? { ...i, unit_price } : i)));
  };

  const handleUpdateDiscount = (itemId: string, discount: number) => {
    setItems(items.map(i => (i.id === itemId ? { ...i, discount } : i)));
  };

  const handleRemoveItem = (itemId: string) => {
    setItems(items.filter(i => i.id !== itemId));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (items.length === 0) {
      setError('Please add at least one item to the sale');
      return;
    }
    if (!user?.shopId) {
      setError('Shop not found');
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const payload: CreateSaleRequest = {
        items: items.map(item => ({
          variant_id: item.variant_id,
          quantity: item.quantity,
          unit_price: item.unit_price,
          discount: item.discount,
        })),
        customer_id: customerId === 'walk_in' ? undefined : customerId,
        discount: totalDiscount,
        payments: [
          {
            amount: netTotal,
            method: paymentMethod === 'cash' ? 'cash' : 'khata',
          },
        ],
      };

      await createSale(payload);
      navigate('/sales');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create sale';
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  const filteredProducts = variants
    .filter(p => p.is_active)
    .filter(p =>
      p.sku.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.product_name.toLowerCase().includes(searchQuery.toLowerCase())
    );

  return (
    <PageContainer maxWidth="full">
      <form onSubmit={handleSubmit}>
        <div className="flex items-center gap-2 mb-2">
          <Link
            to="/sales"
            className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-900 transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Back to Sales History
          </Link>
        </div>

        <PageHeader
          title="POS — New Fabric Counter Sale"
          description="Select cloth rolls, enter cut meters or piece count, and issue customer bill."
        />

        {error && (
          <div className="mb-4 p-3 bg-rose-50 border border-rose-200 rounded-md flex items-center gap-2 text-sm text-rose-700">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            {error}
          </div>
        )}

        {/* POS Two Column Layout */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left 2 Cols: Item Picker and Sale Cart */}
          <div className="lg:col-span-2 space-y-4">
            {/* Customer & Price Mode Selector */}
            <Card>
              <CardContent className="p-4">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-zinc-700 mb-1">Customer Account</label>
                    <Select value={customerId} onChange={e => setCustomerId(e.target.value)}>
                      <option value="walk_in">Walk-in Customer (Cash)</option>
                      <option value="c1">Chaudhry Fabric Traders (Khata)</option>
                      <option value="c2">Haji Muhammad & Sons (Khata)</option>
                    </Select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-zinc-700 mb-1">Customer Phone (Optional)</label>
                    <Input
                      placeholder="0300-1234567"
                      value={customerPhone}
                      onChange={e => setCustomerPhone(e.target.value)}
                    />
                  </div>
                  <div className="flex items-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setShowProductPicker(true)}
                      leftIcon={<Plus className="w-3.5 h-3.5" />}
                    >
                      Add Item
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Cart Table Shell */}
            <Card>
              <CardHeader className="p-4 pb-2 border-b border-zinc-100 flex flex-row items-center justify-between">
                <CardTitle className="text-xs font-semibold uppercase text-zinc-500 tracking-wider">
                  Sale Items & Fabric Cuts
                </CardTitle>
                <Badge variant="neutral" size="sm">
                  {items.length} Item{items.length !== 1 ? 's' : ''}
                </Badge>
              </CardHeader>
              <CardContent className="p-0">
                {items.length === 0 ? (
                  <div className="p-8 text-center text-zinc-500">
                    <p className="text-sm">No items added yet</p>
                    <p className="text-xs text-zinc-400 mt-1">Click "Add Item" to start building the sale</p>
                  </div>
                ) : (
                  <div className="divide-y divide-zinc-100 text-xs">
                    {items.map((item) => (
                      <div key={item.id} className="p-4 flex items-center justify-between gap-4">
                        <div className="flex-1 min-w-0">
                          <h4 className="font-semibold text-zinc-900 truncate">
                            {item.product_name}
                          </h4>
                          <p className="text-[11px] text-zinc-500 font-mono">
                            {item.sku} • {item.unit}
                          </p>
                        </div>
                        <div className="flex items-center gap-3">
                          <div className="flex items-center gap-2">
                            <Input
                              type="number"
                              step="0.01"
                              min="0.01"
                              value={item.quantity}
                              onChange={e => handleUpdateQuantity(item.id, parseFloat(e.target.value) || 0)}
                              className="w-20 text-center text-xs"
                            />
                            <Input
                              type="number"
                              step="1"
                              min="0"
                              value={item.unit_price}
                              onChange={e => handleUpdatePrice(item.id, parseFloat(e.target.value) || 0)}
                              className="w-24 text-center text-xs"
                              placeholder="Price"
                            />
                            <Input
                              type="number"
                              step="1"
                              min="0"
                              value={item.discount}
                              onChange={e => handleUpdateDiscount(item.id, parseFloat(e.target.value) || 0)}
                              className="w-20 text-center text-xs"
                              placeholder="Disc."
                            />
                          </div>
                          <div className="text-right font-semibold text-zinc-900 font-tabular w-24">
                            {formatCurrency(item.quantity * item.unit_price - item.discount)}
                          </div>
                          <button
                            type="button"
                            onClick={() => handleRemoveItem(item.id)}
                            className="text-zinc-400 hover:text-rose-600 p-1 rounded"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Product Picker Modal */}
            {showProductPicker && (
              <Card className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4">
                <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[70vh] flex flex-col">
                  <CardHeader className="p-4 border-b border-zinc-100 flex items-center justify-between">
                    <CardTitle className="text-sm font-semibold text-zinc-900">Select Product</CardTitle>
                    <button
                      type="button"
                      onClick={() => setShowProductPicker(false)}
                      className="text-zinc-400 hover:text-zinc-600"
                    >
                      ✕
                    </button>
                  </CardHeader>
                  <CardContent className="p-4 flex-1 overflow-auto">
                    <Input
                      placeholder="Search by SKU or name..."
                      value={searchQuery}
                      onChange={e => setSearchQuery(e.target.value)}
                      className="mb-4"
                      leftIcon={<Search className="w-4 h-4" />}
                    />
                    {filteredProducts.length === 0 ? (
                      <p className="text-center text-zinc-500 py-8">No products found</p>
                    ) : (
                      <div className="space-y-2">
                        {filteredProducts.map((variant) => (
                          <button
                            key={variant.id}
                            type="button"
                            onClick={() => handleAddItem(variant)}
                            className="w-full p-3 text-left hover:bg-zinc-50 rounded-md border border-zinc-100 transition-colors"
                          >
                            <div className="font-medium text-zinc-900">{variant.sku}</div>
                            <div className="text-xs text-zinc-500">{variant.product_name}</div>
                            <div className="text-xs text-zinc-500">
                              {formatCurrency(variant.selling_price)} / {variant.unit || 'meter'}
                            </div>
                          </button>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </div>
              </Card>
            )}
          </div>

          {/* Right Col: Bill Summary & Checkout */}
          <div className="space-y-4">
            <Card className="sticky top-20">
              <CardHeader className="p-4 border-b border-zinc-100">
                <CardTitle className="text-sm font-semibold text-zinc-900">
                  Payment Summary
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 space-y-4">
                <div className="space-y-2 text-xs border-b border-zinc-100 pb-3">
                  <div className="flex justify-between text-zinc-600">
                    <span>Gross Subtotal</span>
                    <span className="font-tabular font-medium text-zinc-900">
                      {formatCurrency(subtotal)}
                    </span>
                  </div>
                  <div className="flex justify-between text-zinc-600">
                    <span>Discount</span>
                    <span className="font-tabular text-zinc-600">
                      {formatCurrency(totalDiscount)}
                    </span>
                  </div>
                  <div className="flex justify-between text-sm font-bold text-zinc-900 pt-1">
                    <span>Net Payable</span>
                    <span className="font-tabular text-base text-zinc-900">
                      {formatCurrency(netTotal)}
                    </span>
                  </div>
                </div>

                {/* Payment Method Selector */}
                <div className="space-y-2">
                  <label className="block text-xs font-medium text-zinc-700">
                    Settlement Method
                  </label>
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => setPaymentMethod('cash')}
                      className={`p-2.5 rounded-md border text-xs font-medium text-center transition-colors cursor-pointer ${
                        paymentMethod === 'cash'
                          ? 'border-zinc-900 bg-zinc-900 text-white shadow-xs'
                          : 'border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50'
                      }`}
                    >
                      Cash
                    </button>
                    <button
                      type="button"
                      onClick={() => setPaymentMethod('khata')}
                      className={`p-2.5 rounded-md border text-xs font-medium text-center transition-colors cursor-pointer ${
                        paymentMethod === 'khata'
                          ? 'border-zinc-900 bg-zinc-900 text-white shadow-xs'
                          : 'border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50'
                      }`}
                    >
                      Customer Khata
                    </button>
                  </div>
                </div>

                <Button
                  type="submit"
                  variant="primary"
                  size="lg"
                  className="w-full text-sm font-semibold mt-2"
                  disabled={submitting || items.length === 0}
                  leftIcon={submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                >
                  {submitting ? 'Processing...' : `Complete Sale (${formatCurrency(netTotal)})`}
                </Button>
              </CardContent>
            </Card>
          </div>
        </div>
      </form>
    </PageContainer>
  );
}