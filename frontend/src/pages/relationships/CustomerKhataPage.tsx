import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { Plus, Download, Trash2 } from 'lucide-react';
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
  Skeleton,
  Dialog,
} from '../../components/ui/index.ts';
import { formatCurrency, formatDate } from '../../lib/formatters.ts';
import {
  getCustomers,
  getCustomerKhata,
  recordCustomerPayment,
} from '../../lib/api/customers.ts';
import { voidPayment } from '../../lib/api/payments.ts';
import type { Customer, KhataEntry, PaymentMethod } from '../../types/index.ts';

export function CustomerKhataPage() {
  const { id } = useParams<{ id: string }>();
  const [selectedCustomer, setSelectedCustomer] = useState<string>(id || '');
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [khataEntries, setKhataEntries] = useState<KhataEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Payment modal state
  const [isPaymentModalOpen, setIsPaymentModalOpen] = useState(false);
  const [paymentAmount, setPaymentAmount] = useState('');
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>('cash');
  const [paymentReference, setPaymentReference] = useState('');
  const [submittingPayment, setSubmittingPayment] = useState(false);
  const [paymentError, setPaymentError] = useState<string | null>(null);

  useEffect(() => {
    const loadCustomers = async () => {
      try {
        const customersData = await getCustomers();
        setCustomers(customersData);
        if (customersData.length > 0) {
          if (!selectedCustomer || !customersData.some((c) => c.id === selectedCustomer)) {
            const initialId = id && customersData.some((c) => c.id === id) ? id : customersData[0].id;
            setSelectedCustomer(initialId);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load customers');
      }
    };
    loadCustomers();
  }, [id]);

  useEffect(() => {
    if (selectedCustomer) {
      const loadKhata = async () => {
        try {
          setLoading(true);
          setError(null);
          const data = await getCustomerKhata(selectedCustomer);
          setKhataEntries(data);
        } catch (err) {
          setError(err instanceof Error ? err.message : 'Failed to load khata');
        } finally {
          setLoading(false);
        }
      };
      loadKhata();
    }
  }, [selectedCustomer]);

  const currentCustomer = customers.find((c) => c.id === selectedCustomer);
  const outstandingBalance = currentCustomer?.current_balance || 0;

  const handleRecordPayment = async (e: React.FormEvent) => {
    e.preventDefault();
    const amountNum = parseFloat(paymentAmount);
    if (isNaN(amountNum) || amountNum <= 0) {
      setPaymentError('Please enter a valid amount greater than 0');
      return;
    }

    try {
      setSubmittingPayment(true);
      setPaymentError(null);
      await recordCustomerPayment(selectedCustomer, {
        amount: amountNum,
        method: paymentMethod,
        reference: paymentReference.trim() || undefined,
      });

      setIsPaymentModalOpen(false);
      setPaymentAmount('');
      setPaymentReference('');

      // Refresh customers (updates balance) & khata ledger
      const [updatedCustomers, updatedKhata] = await Promise.all([
        getCustomers(),
        getCustomerKhata(selectedCustomer),
      ]);
      setCustomers(updatedCustomers);
      setKhataEntries(updatedKhata);
    } catch (err) {
      setPaymentError(err instanceof Error ? err.message : 'Failed to record payment');
    } finally {
      setSubmittingPayment(false);
    }
  };

  const handleExport = () => {
    window.print();
  };

  const reloadKhata = async () => {
    const [updatedCustomers, updatedKhata] = await Promise.all([
      getCustomers(),
      getCustomerKhata(selectedCustomer),
    ]);
    setCustomers(updatedCustomers);
    setKhataEntries(updatedKhata);
  };

  const handleVoidPayment = async (entry: KhataEntry) => {
    if (!window.confirm(`Void this payment of ${formatCurrency(entry.credit)}? The Khata balance will be restored.`)) {
      return;
    }
    try {
      setError(null);
      await voidPayment(entry.id);
      await reloadKhata();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to void payment');
    }
  };

  return (
    <>
      {error && (
        <div className="mb-4 p-3 bg-rose-50 border border-rose-200 rounded-md text-sm text-rose-700">
          {error}
        </div>
      )}
      <PageContainer>
        <PageHeader
          title="Customer Khata Ledger (Receivables)"
          description="Track credit sales, partial cash collections, receipts, and outstanding khata balance."
          breadcrumbs={[
            { label: 'Relationships', href: '/customers' },
            { label: 'Customer Khata' },
          ]}
          actions={
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                leftIcon={<Download className="w-4 h-4" />}
                onClick={handleExport}
              >
                Export Statement
              </Button>
              <Button
                variant="primary"
                size="sm"
                leftIcon={<Plus className="w-4 h-4" />}
                onClick={() => {
                  setPaymentError(null);
                  setPaymentAmount(outstandingBalance > 0 ? String(outstandingBalance) : '');
                  setIsPaymentModalOpen(true);
                }}
                disabled={!selectedCustomer}
              >
                Record Payment
              </Button>
            </div>
          }
        />

        {/* Customer Selector & Net Balance Banner */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
          <div className="sm:col-span-2">
            <Card>
              <CardContent className="p-4">
                <Select
                  label="Select Customer Account"
                  value={selectedCustomer}
                  onChange={(e) => setSelectedCustomer(e.target.value)}
                >
                  {customers.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} {c.phone && `(${c.phone})`}
                    </option>
                  ))}
                </Select>
              </CardContent>
            </Card>
          </div>

          <Card
            className={
              outstandingBalance >= 0
                ? 'bg-zinc-900 text-white border-zinc-900'
                : 'bg-emerald-900 text-white border-emerald-900'
            }
          >
            <CardContent className="p-4 flex flex-col justify-center">
              <span className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
                {outstandingBalance >= 0 ? 'Outstanding Receivable' : 'Advance Balance'}
              </span>
              <div className="text-2xl font-bold font-tabular text-white mt-0.5">
                {formatCurrency(Math.abs(outstandingBalance))}
              </div>
              <span className="text-[10px] text-zinc-400 mt-1">
                {outstandingBalance >= 0 ? 'Customer owes store' : 'Store owes customer'}
              </span>
            </CardContent>
          </Card>
        </div>

        {/* Khata Ledger Table Shell */}
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Transaction Description</TableHead>
                <TableHead>Ref / Invoice</TableHead>
                <TableHead align="right">Debit (Sale +)</TableHead>
                <TableHead align="right">Credit (Payment -)</TableHead>
                <TableHead align="right">Running Balance</TableHead>
                <TableHead align="right">Actions</TableHead>
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
                    <TableCell align="right"><Skeleton className="h-4 w-16" /></TableCell>
                  </TableRow>
                ))
              ) : khataEntries.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center py-8 text-zinc-500">
                    No khata entries for this customer
                  </TableCell>
                </TableRow>
              ) : (
                khataEntries.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell className="text-xs text-zinc-600">
                      {formatDate(entry.entry_date, true)}
                    </TableCell>
                    <TableCell className={`font-medium ${entry.debit > 0 ? 'text-zinc-900' : 'text-emerald-800'}`}>
                      {entry.notes || (entry.debit > 0 ? 'Sale on Credit' : 'Payment Received')}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-zinc-500">
                      {entry.reference || '—'}
                    </TableCell>
                    <TableCell align="right" className="font-tabular text-rose-700 font-semibold">
                      {entry.debit > 0 ? formatCurrency(entry.debit) : '—'}
                    </TableCell>
                    <TableCell align="right" className="font-tabular text-emerald-700 font-semibold">
                      {entry.credit > 0 ? formatCurrency(entry.credit) : '—'}
                    </TableCell>
                    <TableCell align="right" className="font-tabular font-bold text-zinc-900">
                      {formatCurrency(entry.balance_after)}
                    </TableCell>
                    <TableCell align="right">
                      {entry.entry_type === 'payment' ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 px-0 text-rose-600 hover:bg-rose-50"
                          title="Void this payment"
                          onClick={() => handleVoidPayment(entry)}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      ) : (
                        <span className="text-xs text-zinc-300">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </Card>

        {/* Record Payment Dialog */}
        <Dialog
          open={isPaymentModalOpen}
          onClose={() => setIsPaymentModalOpen(false)}
          title={`Record Customer Payment - ${currentCustomer?.name || ''}`}
          description={`Outstanding balance: ${formatCurrency(outstandingBalance)}`}
          footer={
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setIsPaymentModalOpen(false)}
                disabled={submittingPayment}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={handleRecordPayment}
                isLoading={submittingPayment}
                disabled={!paymentAmount || parseFloat(paymentAmount) <= 0}
              >
                Save Payment
              </Button>
            </>
          }
        >
          <form onSubmit={handleRecordPayment} className="space-y-4">
            {paymentError && (
              <div className="p-2.5 bg-rose-50 border border-rose-200 rounded text-xs text-rose-700">
                {paymentError}
              </div>
            )}
            <div>
              <Input
                label="Payment Amount (PKR)"
                type="number"
                step="any"
                min="0.01"
                placeholder="e.g. 500"
                value={paymentAmount}
                onChange={(e) => setPaymentAmount(e.target.value)}
                required
              />
            </div>
            <div>
              <Select
                label="Payment Method"
                value={paymentMethod}
                onChange={(e) => setPaymentMethod(e.target.value as PaymentMethod)}
              >
                <option value="cash">Cash</option>
                <option value="bank">Bank Transfer</option>
                <option value="jazzcash">JazzCash</option>
                <option value="easypaisa">EasyPaisa</option>
                <option value="card">Debit / Credit Card</option>
                <option value="other">Other</option>
              </Select>
            </div>
            <div>
              <Input
                label="Reference / Receipt / Notes"
                placeholder="Optional receipt number or bank transaction ID"
                value={paymentReference}
                onChange={(e) => setPaymentReference(e.target.value)}
              />
            </div>
          </form>
        </Dialog>
      </PageContainer>
    </>
  );
}