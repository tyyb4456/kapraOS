import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { BookOpen, Phone, MapPin, StickyNote, Package } from 'lucide-react';
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
  Tabs,
  TabList,
  TabTrigger,
  TabContent,
} from '../../components/ui/index.ts';
import { formatCurrency, formatDate, formatQuantity } from '../../lib/formatters.ts';
import {
  getSupplier,
  getSupplierBalance,
  getSupplierKhata,
  type SupplierBalance,
} from '../../lib/api/suppliers.ts';
import { getPurchases, getPurchase } from '../../lib/api/purchases.ts';
import { getInventory } from '../../lib/api/inventory.ts';
import type {
  Supplier,
  KhataEntry,
  Purchase,
  PurchaseDetail,
  InventoryItem,
} from '../../types/index.ts';

interface VariantInfo {
  product_name: string;
  sku: string;
  unit: string;
  attributes: Record<string, string>;
}

export function SupplierProfilePage() {
  const { id } = useParams<{ id: string }>();
  const [supplier, setSupplier] = useState<Supplier | null>(null);
  const [balance, setBalance] = useState<SupplierBalance | null>(null);
  const [khataEntries, setKhataEntries] = useState<KhataEntry[]>([]);
  const [purchaseDetails, setPurchaseDetails] = useState<PurchaseDetail[]>([]);
  const [purchaseStatuses, setPurchaseStatuses] = useState<Record<string, Purchase['status']>>({});
  const [variantMap, setVariantMap] = useState<Record<string, VariantInfo>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');

  useEffect(() => {
    if (!id) return;
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const [supplierData, balanceData, khataData, purchasesData, inventoryData] =
          await Promise.all([
            getSupplier(id),
            getSupplierBalance(id),
            getSupplierKhata(id),
            getPurchases({ supplier_id: id }),
            getInventory(),
          ]);
        setSupplier(supplierData);
        setBalance(balanceData);
        setKhataEntries(khataData);

        const map: Record<string, VariantInfo> = {};
        (inventoryData as InventoryItem[]).forEach((item) => {
          map[item.variant_id] = {
            product_name: item.product_name,
            sku: item.sku,
            unit: item.unit,
            attributes: item.attributes || {},
          };
        });
        setVariantMap(map);

        setPurchaseStatuses(
          Object.fromEntries(purchasesData.map((p) => [p.id, p.status])),
        );
        const details = await Promise.all(
          purchasesData.map((p) => getPurchase(p.id).catch(() => null)),
        );
        setPurchaseDetails(
          (details.filter(Boolean) as unknown as PurchaseDetail[]).sort(
            (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
          ),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load supplier profile');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [id]);

  const toNum = (v: number | string | null | undefined) =>
    typeof v === 'string' ? parseFloat(v) : Number(v) || 0;

  const inRange = (dateStr: string) => {
    const day = dateStr.slice(0, 10);
    if (fromDate && day < fromDate) return false;
    if (toDate && day > toDate) return false;
    return true;
  };

  const filteredEntries = khataEntries.filter((e) => inRange(e.entry_date));

  const groups = new Map<string, PurchaseDetail[]>();
  purchaseDetails
    .filter((p) => inRange(p.created_at))
    .forEach((p) => {
      const day = p.created_at.slice(0, 10);
      if (!groups.has(day)) groups.set(day, []);
      groups.get(day)!.push(p);
    });
  const groupedPurchases = Array.from(groups.entries()).sort((a, b) => (a[0] < b[0] ? 1 : -1));

  const variantLabel = (variantId: string) => {
    const v = variantMap[variantId];
    if (!v) return 'Unknown item';
    const attrs = Object.values(v.attributes || {}).filter(Boolean).join(', ');
    return attrs ? `${v.product_name} (${attrs})` : v.product_name;
  };

  const getPurchaseStatusBadge = (status?: Purchase['status']) => {
    switch (status) {
      case 'received':
        return <Badge variant="success" size="sm">Received</Badge>;
      case 'pending':
        return <Badge variant="warning" size="sm">Pending Delivery</Badge>;
      case 'ordered':
        return <Badge variant="secondary" size="sm">Ordered</Badge>;
      case 'cancelled':
        return <Badge variant="destructive" size="sm">Cancelled</Badge>;
      default:
        return null;
    }
  };

  if (!loading && !supplier) {
    return (
      <PageContainer>
        <div className="p-3 bg-rose-50 border border-rose-200 rounded-md text-sm text-rose-700">
          {error || 'Supplier not found'}
        </div>
      </PageContainer>
    );
  }

  const outstanding = balance?.outstanding_balance ?? supplier?.current_balance ?? 0;

  return (
    <PageContainer>
      <PageHeader
        title={supplier?.name || 'Supplier Profile'}
        description="Complete profile: what was bought, when, and the running payable."
        breadcrumbs={[
          { label: 'Relationships', href: '/suppliers' },
          { label: 'Suppliers', href: '/suppliers' },
          { label: supplier?.name || 'Profile' },
        ]}
        actions={
          <Link to={`/suppliers/khata/${id}`}>
            <Button variant="outline" size="sm" leftIcon={<BookOpen className="w-4 h-4" />}>
              Open Khata Ledger
            </Button>
          </Link>
        }
      />

      {error && (
        <div className="mb-4 p-3 bg-rose-50 border border-rose-200 rounded-md text-sm text-rose-700">
          {error}
        </div>
      )}

      {/* Contact + balance header */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <div className="sm:col-span-2">
          <Card>
            <CardContent className="p-4 space-y-2">
              {loading ? (
                <>
                  <Skeleton className="h-4 w-48" />
                  <Skeleton className="h-4 w-64" />
                  <Skeleton className="h-4 w-56" />
                </>
              ) : (
                <>
                  <div className="flex items-center gap-2 text-sm text-zinc-700">
                    <Phone className="w-3.5 h-3.5 text-zinc-400" />
                    <span className="font-mono text-xs">{supplier?.phone || 'No phone'}</span>
                  </div>
                  <div className="flex items-center gap-2 text-sm text-zinc-700">
                    <MapPin className="w-3.5 h-3.5 text-zinc-400" />
                    <span>{supplier?.address || 'No address'}</span>
                  </div>
                  {supplier?.notes && (
                    <div className="flex items-start gap-2 text-sm text-zinc-600">
                      <StickyNote className="w-3.5 h-3.5 text-zinc-400 mt-0.5" />
                      <span>{supplier.notes}</span>
                    </div>
                  )}
                  <div className="text-[11px] text-zinc-400 pt-1">
                    Supplier since {formatDate(supplier?.created_at)}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>

        <Card
          className={
            outstanding >= 0
              ? 'bg-zinc-900 text-white border-zinc-900'
              : 'bg-emerald-900 text-white border-emerald-900'
          }
        >
          <CardContent className="p-4 flex flex-col justify-center">
            <span className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
              {outstanding >= 0 ? 'Outstanding Payable' : 'Advance to Supplier'}
            </span>
            <div className="text-2xl font-bold font-tabular text-white mt-0.5">
              {loading ? <Skeleton className="h-7 w-28 bg-zinc-700" /> : formatCurrency(Math.abs(outstanding))}
            </div>
            <span className="text-[10px] text-zinc-400 mt-1">
              {outstanding >= 0 ? 'Shop owes supplier' : 'Supplier owes shop'}
            </span>
          </CardContent>
        </Card>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
        <Card>
          <CardContent className="p-4">
            <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">Total Purchases</div>
            <div className="text-lg font-bold font-tabular text-zinc-900 mt-1">
              {loading ? <Skeleton className="h-6 w-20" /> : formatCurrency(balance?.total_purchases ?? 0)}
            </div>
            <div className="text-[10px] text-zinc-400 mt-0.5">
              {balance?.number_of_purchases ?? 0} bill(s)
              {balance?.last_purchase_at && ` · last ${formatDate(balance.last_purchase_at)}`}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">Total Paid</div>
            <div className="text-lg font-bold font-tabular text-emerald-700 mt-1">
              {loading ? <Skeleton className="h-6 w-20" /> : formatCurrency(balance?.total_payments ?? 0)}
            </div>
            <div className="text-[10px] text-zinc-400 mt-0.5">
              {balance?.number_of_payments ?? 0} payment(s)
              {balance?.last_payment_at && ` · last ${formatDate(balance.last_payment_at)}`}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">Total Returns</div>
            <div className="text-lg font-bold font-tabular text-zinc-900 mt-1">
              {loading ? <Skeleton className="h-6 w-20" /> : formatCurrency(balance?.total_returns ?? 0)}
            </div>
            <div className="text-[10px] text-zinc-400 mt-0.5">
              {balance?.number_of_returns ?? 0} return(s)
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">Status</div>
            <div className="mt-2">
              {loading ? (
                <Skeleton className="h-5 w-16" />
              ) : outstanding <= 0 ? (
                <Badge variant="success" size="sm">Clear</Badge>
              ) : (
                <Badge variant="warning" size="sm">Payable Due</Badge>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Date filter */}
      <Card className="mb-4">
        <CardContent className="p-3.5 sm:p-4 flex flex-wrap items-end gap-3">
          <div className="w-40">
            <Input
              label="From date"
              type="date"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
            />
          </div>
          <div className="w-40">
            <Input
              label="To date"
              type="date"
              value={toDate}
              onChange={(e) => setToDate(e.target.value)}
            />
          </div>
          {(fromDate || toDate) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setFromDate('');
                setToDate('');
              }}
            >
              Clear
            </Button>
          )}
        </CardContent>
      </Card>

      {/* Transactions + Purchases */}
      <Tabs defaultValue="purchases">
        <TabList>
          <TabTrigger value="purchases">Purchases ({groupedPurchases.reduce((n, [, ps]) => n + ps.length, 0)})</TabTrigger>
          <TabTrigger value="transactions">Transactions ({filteredEntries.length})</TabTrigger>
        </TabList>

        <TabContent value="purchases">
          {loading ? (
            <Card>
              <CardContent className="p-4 space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-20 w-full" />
                ))}
              </CardContent>
            </Card>
          ) : groupedPurchases.length === 0 ? (
            <Card>
              <CardContent className="p-8 text-center text-sm text-zinc-500">
                {fromDate || toDate
                  ? 'No purchases in this date range'
                  : 'No purchases from this supplier yet'}
              </CardContent>
            </Card>
          ) : (
            groupedPurchases.map(([day, dayPurchases]) => (
              <div key={day} className="mb-5">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
                    {formatDate(day)}
                  </span>
                  <Badge variant="neutral" size="sm">
                    {dayPurchases.length} purchase{dayPurchases.length > 1 ? 's' : ''}
                  </Badge>
                </div>
                <div className="space-y-3">
                  {dayPurchases.map((purchase) => {
                    const total = toNum(purchase.total);
                    const paid = toNum(purchase.paid_amount);
                    const due = toNum(purchase.due_amount ?? total - paid);
                    return (
                      <Card key={purchase.id}>
                        <CardContent className="p-4">
                          <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-zinc-100">
                            <div className="flex items-center gap-2">
                              <Package className="w-4 h-4 text-zinc-400" />
                              <span className="font-mono text-xs font-semibold text-zinc-800">
                                {purchase.invoice_number || `PO-${purchase.id.slice(0, 8)}`}
                              </span>
                              {getPurchaseStatusBadge(purchaseStatuses[purchase.id])}
                            </div>
                            <div className="font-tabular font-bold text-zinc-900">
                              {formatCurrency(total)}
                            </div>
                          </div>

                          <div className="pt-2 space-y-1">
                            {purchase.items.map((item) => (
                              <div
                                key={item.id}
                                className="flex items-center justify-between text-sm"
                              >
                                <span className="text-zinc-800">
                                  {variantLabel(item.variant_id)}
                                </span>
                                <span className="font-tabular text-xs text-zinc-500">
                                  {formatQuantity(
                                    toNum(item.quantity),
                                    variantMap[item.variant_id]?.unit,
                                  )}
                                  {' × '}
                                  {formatCurrency(toNum(item.unit_cost))}
                                  {' = '}
                                  <span className="font-semibold text-zinc-800">
                                    {formatCurrency(
                                      toNum(item.total ?? toNum(item.quantity) * toNum(item.unit_cost)),
                                    )}
                                  </span>
                                </span>
                              </div>
                            ))}
                          </div>

                          <div className="flex items-center justify-end gap-4 pt-2 mt-2 border-t border-zinc-100 text-xs">
                            <span className="text-emerald-700 font-tabular font-semibold">
                              Paid {formatCurrency(paid)}
                            </span>
                            {due > 0 && (
                              <span className="text-rose-600 font-tabular font-semibold">
                                Due {formatCurrency(due)}
                              </span>
                            )}
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </TabContent>

        <TabContent value="transactions">
          <Card>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Reference</TableHead>
                  <TableHead align="right">Credit (Bill +)</TableHead>
                  <TableHead align="right">Debit (Paid -)</TableHead>
                  <TableHead align="right">Running Payable</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  Array.from({ length: 5 }).map((_, i) => (
                    <TableRow key={i}>
                      <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                      <TableCell><Skeleton className="h-4 w-48" /></TableCell>
                      <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                      <TableCell align="right"><Skeleton className="h-4 w-20" /></TableCell>
                      <TableCell align="right"><Skeleton className="h-4 w-20" /></TableCell>
                      <TableCell align="right"><Skeleton className="h-4 w-24" /></TableCell>
                    </TableRow>
                  ))
                ) : filteredEntries.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center py-8 text-zinc-500">
                      {fromDate || toDate
                        ? 'No transactions in this date range'
                        : 'No transactions yet for this supplier'}
                    </TableCell>
                  </TableRow>
                ) : (
                  filteredEntries.map((entry) => (
                    <TableRow key={entry.id}>
                      <TableCell className="text-xs text-zinc-600">
                        {formatDate(entry.entry_date, true)}
                      </TableCell>
                      <TableCell className={`font-medium ${entry.credit > 0 ? 'text-zinc-900' : 'text-emerald-800'}`}>
                        {entry.entry_type === 'return'
                          ? 'Supplier Return'
                          : entry.entry_type === 'purchase'
                          ? (entry.notes || 'Purchase on Credit')
                          : (entry.notes || 'Payment Made')}
                      </TableCell>
                      <TableCell className="font-mono text-xs text-zinc-500">
                        {entry.reference || '—'}
                      </TableCell>
                      <TableCell align="right" className="font-tabular text-zinc-900 font-semibold">
                        {entry.credit > 0 ? formatCurrency(entry.credit) : '—'}
                      </TableCell>
                      <TableCell align="right" className="font-tabular text-emerald-700 font-semibold">
                        {entry.debit > 0 ? formatCurrency(entry.debit) : '—'}
                      </TableCell>
                      <TableCell align="right" className="font-tabular font-bold text-zinc-900">
                        {formatCurrency(entry.balance_after)}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </Card>
        </TabContent>
      </Tabs>
    </PageContainer>
  );
}
