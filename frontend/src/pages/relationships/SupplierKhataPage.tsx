import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Plus, Download } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Button,
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
} from '../../components/ui/index.ts';
import { formatCurrency, formatDate } from '../../lib/formatters.ts';
import { getSuppliers, getSupplierKhata } from '../../lib/api/suppliers.ts';
import type { Supplier, KhataEntry } from '../../types/index.ts';

export function SupplierKhataPage() {
  const { id } = useParams<{ id: string }>();
  const [selectedSupplier, setSelectedSupplier] = useState<string>(id || '');
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [khataEntries, setKhataEntries] = useState<KhataEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true);
        const suppliersData = await getSuppliers();
        setSuppliers(suppliersData);
        if (suppliersData.length > 0) {
          const firstSupplierId = id || suppliersData[0].id;
          setSelectedSupplier(firstSupplierId);
          const khataData = await getSupplierKhata(firstSupplierId);
          setKhataEntries(khataData);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load data');
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, [id]);

  useEffect(() => {
    if (selectedSupplier) {
      const loadKhata = async () => {
        try {
          setLoading(true);
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
            <Button variant="outline" size="sm" leftIcon={<Download />}>
              Export Statement
            </Button>
            <Link to={`/suppliers/${selectedSupplier}/payment`}>
              <Button variant="primary" size="sm" leftIcon={<Plus />}>
                Record Payment Out
              </Button>
            </Link>
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

        <Card className={outstandingBalance >= 0 ? 'bg-zinc-900 text-white border-zinc-900' : 'bg-emerald-900 text-white border-emerald-900'}>
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
                    {formatDate(entry.entry_date, false)}
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
      </PageContainer>
    </>
  );
}