import { useCallback, useEffect, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import {
  ErrorState,
  LoadingState,
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../../components/ui/index.ts';
import { formatCurrency, formatDate, formatQuantity } from '../../lib/formatters.ts';
import { getOpeningInventoryBatch } from '../../lib/api/openingInventory.ts';
import type { OpeningInventoryBatchDetail } from '../../types/index.ts';

export function OpeningInventoryBatchPage() {
  const { batchId } = useParams<{ batchId: string }>();
  const location = useLocation();
  const justPosted = (location.state as { justPosted?: boolean } | null)?.justPosted === true;

  const [batch, setBatch] = useState<OpeningInventoryBatchDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const loadBatch = useCallback(async () => {
    if (!batchId) return;
    try {
      setLoading(true);
      setError(null);
      setNotFound(false);
      setBatch(await getOpeningInventoryBatch(batchId));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load the opening inventory batch';
      if (message.toLowerCase().includes('not found')) {
        setNotFound(true);
      } else {
        setError(message);
      }
    } finally {
      setLoading(false);
    }
  }, [batchId]);

  useEffect(() => {
    void loadBatch();
  }, [loadBatch]);

  return (
    <PageContainer>
      <div className="flex items-center gap-2 mb-2">
        <Link
          to="/inventory/opening"
          className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-900 transition-colors"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to Opening Inventory
        </Link>
      </div>

      {loading ? (
        <LoadingState message="Loading opening inventory batch..." />
      ) : notFound ? (
        <ErrorState
          title="Batch not found"
          message="This opening inventory batch does not exist or belongs to another shop."
        />
      ) : error || !batch ? (
        <ErrorState
          title="Could not load batch"
          message={error || 'Unknown error'}
          onRetry={() => void loadBatch()}
        />
      ) : (
        <>
          <PageHeader
            title="Opening Inventory Batch"
            description={`Posted ${formatDate(batch.created_at)}`}
            breadcrumbs={[
              { label: 'Inventory', href: '/inventory' },
              { label: 'Opening Inventory', href: '/inventory/opening' },
              { label: `Batch ${batch.id.slice(0, 8)}` },
            ]}
          />

          {justPosted && (
            <Alert variant="success" className="mb-4" title="Opening inventory posted">
              Stock has been received and the value was posted as Debit Inventory /
              Credit Opening Balance Equity. No purchase or payable was created.
            </Alert>
          )}

          <Alert variant="info" className="mb-4" title="One opening entry per variant, ever">
            Opening balances can&apos;t be edited or voided: later stock has already been
            averaged into these costs. Use a stock adjustment for corrections.
          </Alert>

          <Card className="mb-4">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-sm font-semibold">Summary</CardTitle>
              {batch.source === 'import' ? (
                <Badge variant="info" size="sm">Imported</Badge>
              ) : (
                <Badge variant="neutral" size="sm">Manual</Badge>
              )}
            </CardHeader>
            <CardContent>
              <dl className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
                <div>
                  <dt className="text-zinc-500">Lines posted</dt>
                  <dd className="mt-0.5 font-tabular text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                    {batch.entry_count}
                  </dd>
                </div>
                <div>
                  <dt className="text-zinc-500">Estimated costs</dt>
                  <dd className="mt-0.5 font-tabular text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                    {batch.estimated_entry_count}
                  </dd>
                </div>
                <div>
                  <dt className="text-zinc-500">Total value</dt>
                  <dd className="mt-0.5 font-tabular text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                    {formatCurrency(Number(batch.total_value))}
                  </dd>
                </div>
                <div>
                  <dt className="text-zinc-500">Notes</dt>
                  <dd className="mt-0.5 text-zinc-700 dark:text-zinc-300">{batch.notes || '—'}</dd>
                </div>
              </dl>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Opening Lines</CardTitle>
            </CardHeader>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Variant SKU</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead align="right">Quantity</TableHead>
                  <TableHead align="right">Unit Cost</TableHead>
                  <TableHead>Cost Status</TableHead>
                  <TableHead align="right">Value</TableHead>
                  <TableHead>Stock Movement</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {batch.entries.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell className="font-mono text-xs text-zinc-500">
                      {entry.sku || '—'}
                    </TableCell>
                    <TableCell className="font-medium text-zinc-900 dark:text-zinc-100">
                      {entry.product_name || '—'}
                    </TableCell>
                    <TableCell align="right" className="font-tabular">
                      {formatQuantity(Number(entry.quantity), entry.unit)}
                    </TableCell>
                    <TableCell align="right" className="font-tabular text-zinc-600">
                      {formatCurrency(Number(entry.unit_cost))}
                    </TableCell>
                    <TableCell>
                      {entry.cost_status === 'estimated' ? (
                        <Badge variant="warning" size="sm">Estimated</Badge>
                      ) : (
                        <Badge variant="success" size="sm">Known</Badge>
                      )}
                    </TableCell>
                    <TableCell align="right" className="font-tabular font-semibold text-zinc-900 dark:text-zinc-100">
                      {formatCurrency(Number(entry.value))}
                    </TableCell>
                    <TableCell className="font-mono text-[11px] text-zinc-500">
                      {entry.movement
                        ? `${entry.movement.movement_type} · ${entry.movement.id.slice(0, 8)}`
                        : '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>

          <div className="mt-4 flex justify-end">
            <Link to="/inventory">
              <Button variant="outline" size="sm">
                View Stock Levels
              </Button>
            </Link>
          </div>
        </>
      )}
    </PageContainer>
  );
}
