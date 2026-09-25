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
import { getCustomers } from '../../lib/api/customers.ts';
import { useAuth } from '../../context/AuthContext.tsx';
import type { ProductVariant, Customer, PaymentMethod } from '../../types/index.ts';

type SettlementOption = PaymentMethod | 'khata';

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
  const [paymentMethod, setPaymentMethod] = useState<SettlementOption>('cash');
  const [customerId, setCustomerId] = useState<string>('walk_in');
  const [customerPhone, setCustomerPhone] = useState('');
  const [items, setItems] = useState<SaleItem[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [showProductPicker, setShowProductPicker] = useState(false);
  const [variants, setVariants] = useState<VariantWithProduct[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
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

  useEffect(() => {
    const loadCustomers = async () => {
      try {
        const data = await getCustomers();
        setCustomers(data);
      } catch (err) {
        console.error('Failed to load customers:', err);
      }
    };
    loadCustomers();
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
          unit_price: Number(variant.selling_price) || 0,
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

    if (paymentMethod === 'khata' && (customerId === 'walk_in' || !customerId)) {
      setError('Please select a customer account to sell on Customer Khata (credit).');
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
        discount: 0,
        payments:
          paymentMethod === 'khata'
            ? []
            : [
                {
                  amount: netTotal,
                  method: paymentMethod,
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
    .filter(p => p.is_active !== false)
    .filter(p =>
      (p.sku || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
      (p.product_name || '').toLowerCase().includes(searchQuery.toLowerCase())
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
                      {customers.map(c => (
                        <option key={c.id} value={c.id}>
                          {c.name}{c.phone ? ` (${c.phone})` : ''}{c.current_balance > 0 ? ` — Balance: ${formatCurrency(c.current_balance)}` : ''}
                        </option>
                      ))}
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
                    {/* Column headers — so shopkeeper knows what each value is */}
                    <div className="hidden sm:flex items-center gap-3 px-4 pt-3 pb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                      <div className="flex-1 min-w-0">Item</div>
                      <div className="w-24 text-center">Quantity</div>
                      <div className="w-28 text-center">Price (Rs)</div>
                      <div className="w-24 text-center">Discount (Rs)</div>
                      <div className="w-24 text-right">Total</div>
                      <div className="w-7" />
                    </div>
                    {items.map((item) => (
                      <div key={item.id} className="p-4 flex items-start justify-between gap-4">
                        <div className="flex-1 min-w-0 pt-5">
                          <h4 className="font-semibold text-zinc-900 truncate">
                            {item.product_name}
                          </h4>
                          <p className="text-[11px] text-zinc-500 font-mono">
                            {item.sku} • {item.unit}
                          </p>
                        </div>
                        <div className="flex items-start gap-2 sm:gap-3">
                          <div className="flex flex-col gap-1 w-20 sm:w-24">
                            <label className="text-[11px] font-medium text-zinc-600 text-center leading-none">
                              Qty
                              <span className="block font-normal text-zinc-400">
                                {item.unit || 'qty'}
                              </span>
                            </label>
                            <Input
                              type="number"
                              step="0.01"
                              min="0.01"
                              value={item.quantity}
                              onChange={e => handleUpdateQuantity(item.id, parseFloat(e.target.value) || 0)}
                              className="w-full text-center text-xs"
                              placeholder="Qty"
                              aria-label={`Quantity in ${item.unit || 'units'}`}
                            />
                          </div>
                          <div className="flex flex-col gap-1 w-24 sm:w-28">
                            <label className="text-[11px] font-medium text-zinc-600 text-center leading-none">
                              Price
                              <span className="block font-normal text-zinc-400">Rs</span>
                            </label>
                            <Input
                              type="number"
                              step="1"
                              min="0"
                              value={item.unit_price}
                              onChange={e => handleUpdatePrice(item.id, parseFloat(e.target.value) || 0)}
                              className="w-full text-center text-xs"
                              placeholder="Price"
                              aria-label="Unit price in Rs"
                            />
                          </div>
                          <div className="flex flex-col gap-1 w-20 sm:w-24">
                            <label className="text-[11px] font-medium text-zinc-600 text-center leading-none">
                              Discount
                              <span className="block font-normal text-zinc-400">Rs</span>
                            </label>
                            <Input
                              type="number"
                              step="1"
                              min="0"
                              value={item.discount}
                              onChange={e => handleUpdateDiscount(item.id, parseFloat(e.target.value) || 0)}
                              className="w-full text-center text-xs"
                              placeholder="Disc."
                              aria-label="Discount in Rs"
                            />
                          </div>
                        </div>
                        <div className="flex items-start gap-1 pt-5">
                          <div className="text-right font-semibold text-zinc-900 font-tabular w-20 sm:w-24">
                            <span className="block sm:hidden text-[10px] font-normal text-zinc-400 uppercase">Total</span>
                            {formatCurrency(item.quantity * item.unit_price - item.discount)}
                          </div>
                          <button
                            type="button"
                            onClick={() => handleRemoveItem(item.id)}
                            className="text-zinc-400 hover:text-rose-600 p-1 rounded"
                            title="Remove item"
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
                            className="w-full p-3 text-left hover:bg-zinc-50 rounded-md border border-zinc-200 transition-colors flex items-center justify-between group cursor-pointer"
                          >
                            <div>
                              <div className="font-medium text-zinc-900 group-hover:text-zinc-950">{variant.product_name}</div>
                              <div className="text-xs text-zinc-500 font-mono mt-0.5">SKU: {variant.sku}</div>
                            </div>
                            <div className="text-right">
                              <div className="font-semibold text-zinc-900">{formatCurrency(variant.selling_price)}</div>
                              <div className="text-xs text-zinc-500 capitalize">per {variant.unit || 'meter'}</div>
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
                  <div className="flex items-center justify-between">
                    <label className="block text-xs font-medium text-zinc-700">
                      Settlement Method
                    </label>
                    {paymentMethod === 'khata' && (
                      <Badge variant="warning" size="sm">
                        Credit / Khata
                      </Badge>
                    )}
                  </div>
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
                    <button
                      type="button"
                      onClick={() => setPaymentMethod('card')}
                      className={`p-2.5 rounded-md border text-xs font-medium text-center transition-colors cursor-pointer ${
                        paymentMethod === 'card'
                          ? 'border-zinc-900 bg-zinc-900 text-white shadow-xs'
                          : 'border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50'
                      }`}
                    >
                      Card
                    </button>
                    <button
                      type="button"
                      onClick={() => setPaymentMethod('bank')}
                      className={`p-2.5 rounded-md border text-xs font-medium text-center transition-colors cursor-pointer ${
                        paymentMethod === 'bank'
                          ? 'border-zinc-900 bg-zinc-900 text-white shadow-xs'
                          : 'border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50'
                      }`}
                    >
                      Bank Transfer
                    </button>
                    <button
                      type="button"
                      onClick={() => setPaymentMethod('jazzcash')}
                      className={`p-2.5 rounded-md border text-xs font-medium text-center transition-colors cursor-pointer ${
                        paymentMethod === 'jazzcash'
                          ? 'border-zinc-900 bg-zinc-900 text-white shadow-xs'
                          : 'border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50'
                      }`}
                    >
                      JazzCash
                    </button>
                    <button
                      type="button"
                      onClick={() => setPaymentMethod('easypaisa')}
                      className={`p-2.5 rounded-md border text-xs font-medium text-center transition-colors cursor-pointer ${
                        paymentMethod === 'easypaisa'
                          ? 'border-zinc-900 bg-zinc-900 text-white shadow-xs'
                          : 'border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50'
                      }`}
                    >
                      EasyPaisa
                    </button>
                  </div>
                  {paymentMethod === 'khata' && customerId === 'walk_in' && (
                    <p className="text-[11px] text-amber-600 font-medium">
                      ⚠️ Please select a Customer Account above to post this sale to Khata.
                    </p>
                  )}
                  {paymentMethod === 'khata' && customerId !== 'walk_in' && (
                    <p className="text-[11px] text-zinc-500">
                      ℹ️ Bill will be added to the customer's Khata receivable.
                    </p>
                  )}
                </div>

                <Button
                  type="submit"
                  variant="primary"
                  size="lg"
                  className="w-full text-sm font-semibold mt-2"
                  disabled={submitting || items.length === 0}
                  leftIcon={submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                >
                  {submitting
                    ? 'Processing...'
                    : paymentMethod === 'khata'
                    ? `Post to Khata (${formatCurrency(netTotal)})`
                    : `Complete Sale (${formatCurrency(netTotal)})`}
                </Button>
              </CardContent>
            </Card>
          </div>
        </div>
      </form>
    </PageContainer>
  );
}