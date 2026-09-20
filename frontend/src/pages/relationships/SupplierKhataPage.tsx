import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { Plus, Download } from 'lucide-react';
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
  getSuppliers,
  getSupplierKhata,
  recordSupplierPayment,
} from '../../lib/api/suppliers.ts';
import type { Supplier, KhataEntry, PaymentMethod } from '../../types/index.ts';

export function SupplierKhataPage() {
  const { id } = useParams<{ id: string }>();
  const [selectedSupplier, setSelectedSupplier] = useState<string>(id || '');
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
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
    const loadSuppliers = async () => {
      try {
        const suppliersData = await getSuppliers();
        setSuppliers(suppliersData);
        if (suppliersData.length > 0) {
          if (!selectedSupplier || !suppliersData.some((s) => s.id === selectedSupplier)) {
            const initialId = id && suppliersData.some((s) => s.id === id) ? id : suppliersData[0].id;
            setSelectedSupplier(initialId);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load suppliers');
      }
    };
    loadSuppliers();
  }, [id]);

  useEffect(() => {
    if (selectedSupplier) {
      const loadKhata = async () => {
        try {
          setLoading(true);
          setError(null);
          const data = await getSupplierKhata(selectedSupplier);
          setKhataEntries(data);
        } catch (err) {
          setError(err instanceof Error ? err.message : 'Failed to load khata');
        } finally {
          setLoading(false);
        }
      };
      loadKhata();
    }
  }, [selectedSupplier]);

  const currentSupplier = suppliers.find((s) => s.id === selectedSupplier);
  const outstandingBalance = currentSupplier?.current_balance || 0;

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
      await recordSupplierPayment(selectedSupplier, {
        amount: amountNum,
        method: paymentMethod,
        reference: paymentReference.trim() || undefined,
      });

      setIsPaymentModalOpen(false);
      setPaymentAmount('');
      setPaymentReference('');

      // Refresh suppliers (updates balance) & khata ledger
      const [updatedSuppliers, updatedKhata] = await Promise.all([
        getSuppliers(),
        getSupplierKhata(selectedSupplier),
      ]);
      setSuppliers(updatedSuppliers);
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

  return (
    <>
      {error && (
        <div className="mb-4 p-3 bg-rose-50 border border-rose-200 rounded-md text-sm text-rose-700">
          {error}
        </div>
      )}
      <PageContainer>
        <PageHeader
          title="Supplier Khata Ledger (Payables)"
          description="Track inward consignments, mill billing, bank payments, and remaining balances owed."
          breadcrumbs={[
            { label: 'Relationships', href: '/suppliers' },
            { label: 'Supplier Khata' },
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
                disabled={!selectedSupplier}
              >
                Record Payment Out
              </Button>
            </div>
          }
        />

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
          <div className="sm:col-span-2">
            <Card>
              <CardContent className="p-4">
                <Select
                  label="Select Supplier Account"
                  value={selectedSupplier}
                  onChange={(e) => setSelectedSupplier(e.target.value)}
                >
                  {suppliers.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} {s.phone && `(${s.phone})`}
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
                {outstandingBalance >= 0 ? 'Outstanding Payable' : 'Advance to Supplier'}
              </span>
              <div className="text-2xl font-bold font-tabular text-white mt-0.5">
                {formatCurrency(Math.abs(outstandingBalance))}
              </div>
              <span className="text-[10px] text-zinc-400 mt-1">
                {outstandingBalance >= 0 ? 'Shop owes supplier' : 'Supplier owes shop'}
              </span>
            </CardContent>
          </Card>
        </div>

        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Consignment / Description</TableHead>
                <TableHead>PO / Bilty #</TableHead>
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
              ) : khataEntries.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-8 text-zinc-500">
                    No khata entries for this supplier
                  </TableCell>
                </TableRow>
              ) : (
                khataEntries.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell className="text-xs text-zinc-600">
                      {formatDate(entry.entry_date, true)}
                    </TableCell>
                    <TableCell className={`font-medium ${entry.credit > 0 ? 'text-zinc-900' : 'text-emerald-800'}`}>
                      {entry.notes || (entry.credit > 0 ? 'Purchase on Credit' : 'Payment Made')}
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

        {/* Record Payment Out Dialog */}
        <Dialog
          open={isPaymentModalOpen}
          onClose={() => setIsPaymentModalOpen(false)}
          title={`Record Supplier Payment - ${currentSupplier?.name || ''}`}
          description={`Outstanding payable: ${formatCurrency(outstandingBalance)}`}
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
                Save Payment Out
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
                placeholder="e.g. 10000"
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
                label="Reference / Cheque / Bilty / Notes"
                placeholder="Optional cheque #, bilty receipt, or bank transfer reference"
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