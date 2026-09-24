// Auth & Identity
export type UserRole = 'owner' | 'admin' | 'manager' | 'cashier';

export interface AuthMeResponse {
  id: string;
  clerk_user_id: string;
  shop_id: string;
  role: UserRole | string | null;
}

export interface CurrentUser {
  id: string;
  clerkUserId: string;
  shopId: string;
  role: UserRole | string | null;
  email?: string;
  fullName?: string;
}

// Product & Catalog
export type UnitOfMeasure = 'meters' | 'yards' | 'pieces' | 'sets' | 'rolls';

export interface Category {
  id: string;
  shop_id: string;
  name: string;
  code?: string | null;
  parent_id?: string | null;
  variants_count?: number;
  created_at: string;
}

export interface ProductVariant {
  id: string;
  product_id: string;
  sku: string;
  barcode?: string | null;
  purchase_price: number;
  selling_price: number;
  unit: string;
  attributes: Record<string, string>;
  is_active: boolean;
}

export interface Product {
  id: string;
  shop_id: string;
  name: string;
  code?: string | null;
  category_id?: string | null;
  category?: Category | null;
  unit: UnitOfMeasure;
  description?: string | null;
  variants_count?: number;
  variants?: ProductVariant[];
  created_at: string;
}

// Inventory
export interface InventoryItem {
  id: string;
  variant_id: string;
  sku: string;
  product_name: string;
  unit: UnitOfMeasure;
  attributes: Record<string, string>;
  quantity_on_hand: number;
  quantity?: number;
  cost_price: number;
  selling_price: number;
  low_stock_threshold?: number;
}

export type MovementType =
  | 'purchase_in'
  | 'sale_out'
  | 'return_in'
  | 'return_out'
  | 'adjustment'
  | 'transfer'
  | 'purchase'
  | 'sale'
  | 'customer_return'
  | 'supplier_return'
  | 'damage';

export interface StockMovement {
  id: string;
  variant_id: string;
  sku: string;
  product_name: string;
  movement_type: MovementType;
  quantity: number;
  unit: UnitOfMeasure;
  reference_type?: string | null;
  reference_id?: string | null;
  notes?: string | null;
  created_at: string;
}

// Customer & Khata
export interface Customer {
  id: string;
  shop_id: string;
  name: string;
  phone?: string | null;
  email?: string | null;
  current_balance: number;
  credit_limit?: number | null;
  created_at: string;
}

export interface KhataEntry {
  id: string;
  party_id: string;
  party_name: string;
  entry_date: string;
  entry_type: 'sale' | 'payment' | 'purchase' | 'return' | 'adjustment';
  debit: number;
  credit: number;
  balance_after: number;
  reference?: string | null;
  notes?: string | null;
}

// Supplier & Payables
export interface Supplier {
  id: string;
  shop_id: string;
  name: string;
  phone?: string | null;
  address?: string | null;
  notes?: string | null;
  current_balance: number;
  created_at: string;
}

// Sales & POS
export type SaleStatus = 'completed' | 'partial' | 'cancelled' | 'returned';
export type PaymentMethod = 'cash' | 'card' | 'bank' | 'jazzcash' | 'easypaisa' | 'other';

export interface SaleItem {
  id?: string;
  variant_id: string;
  product_name: string;
  sku: string;
  unit: UnitOfMeasure;
  quantity: number;
  unit_price: number;
  subtotal: number;
}

export interface Sale {
  id: string;
  shop_id: string;
  invoice_number: string;
  customer_id?: string | null;
  customer_name?: string | null;
  status: SaleStatus;
  payment_method?: PaymentMethod | null;
  subtotal: number;
  discount: number;
  total_amount: number;
  paid_amount: number;
  due_amount?: number;
  items_count: number;
  items?: SaleItem[];
  created_at: string;
}

// Purchases
export type PurchaseStatus = 'received' | 'pending' | 'ordered' | 'cancelled';

export interface PurchaseItem {
  id?: string;
  variant_id: string;
  product_name: string;
  sku: string;
  unit: UnitOfMeasure;
  quantity: number;
  unit_cost: number;
  subtotal: number;
}

export interface Purchase {
  id: string;
  shop_id: string;
  order_number: string;
  supplier_id?: string | null;
  supplier_name?: string | null;
  status: PurchaseStatus;
  total_amount: number;
  paid_amount: number;
  items_count: number;
  items?: PurchaseItem[];
  created_at: string;
}

// Expenses
export interface Expense {
  id: string;
  shop_id: string;
  category: string;
  amount: number;
  payment_method: string;
  description: string;
  date: string;
  receipt_ref?: string | null;
  created_at: string;
}

// Reports & Financial Summary
export interface FinancialSummary {
  period_start: string;
  period_end: string;
  total_sales: number;
  total_cogs: number;
  gross_profit: number;
  total_expenses: number;
  net_profit: number;
  receivables: number;
  payables: number;
}

// Shop Settings
export interface ShopSettings {
  id: string;
  name: string;
  phone: string | null;
  address: string | null;
  currency: string;
  tax_id: string | null;
  default_unit: string;
  created_at: string;
  updated_at: string;
}

export interface UpdateShopSettingsRequest {
  name?: string;
  phone?: string | null;
  address?: string | null;
  currency?: string;
  tax_id?: string | null;
  default_unit?: string;
}

// Dashboard (GET /reports/dashboard)
export interface DashboardSummary {
  as_of: string;
  today_sales: number;
  today_sales_count: number;
  today_payments_received: number;
  today_payment_count: number;
  today_purchases: number;
  today_purchase_count: number;
  today_cogs: number;
  today_gross_profit: number;
  today_expenses: number;
  today_net_profit: number;
  receivables_outstanding: number;
  payables_outstanding: number;
  inventory_quantity: number;
  inventory_estimated_value: number;
  low_stock_variant_count: number | null;
  low_stock_available: boolean;
  notes: string[];
}
