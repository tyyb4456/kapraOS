import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, PackagePlus } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Badge,
  Button,
  Card,
  CardContent,
  EmptyState,
  Select,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../../components/ui/index.ts';
import { formatCurrency, formatDate } from '../../lib/formatters.ts';
import { getOpeningInventoryBatches } from '../../lib/api/openingInventory.ts';
import type { OpeningInventoryBatch, OpeningInventorySource } from '../../types/index.ts';

export function OpeningInventoryPage() {
  const [batches, setBatches] = useState<OpeningInventoryBatch[]>([]);
  const [sourceFilter, setSourceFilter] = useState<'all' | OpeningInventorySource>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadBatches = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getOpeningInventoryBatches(
        sourceFilter === 'all' ? undefined : { source: sourceFilter },
      );
      setBatches(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load opening inventory batches');
    } finally {
      setLoading(false);
    }
  }, [sourceFilter]);

  useEffect(() => {
    void loadBatches();
  }, [loadBatches]);

  return (
    <PageContainer>
      <PageHeader
        title="Opening Inventory"
        description="Your one-time starting stock, posted as Opening Balance Equity — not as a purchase."
        breadcrumbs={[
          { label: 'Inventory', href: '/inventory' },
          { label: 'Opening Inventory' },
        ]}
        actions={
          <Link to="/inventory/opening/new">
            <Button size="sm" leftIcon={<PackagePlus className="w-4 h-4" />}>
              New Opening Batch
            </Button>
          </Link>
        }
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <div className="flex flex-col sm:flex-row items-center gap-3">
            <div className="flex-1 w-full" />
            <div className="w-full sm:w-48">
              <Select
                value={sourceFilter}
                onChange={(e) => setSourceFilter(e.target.value as 'all' | OpeningInventorySource)}
              >
                <option value="all">All Sources</option>
                <option value="manual">Entered Manually</option>
                <option value="import">Imported from File</option>
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

      {!loading && batches.length === 0 && !error ? (
        <EmptyState
          icon={<PackagePlus className="w-5 h-5" />}
          title="No opening stock recorded yet"
          description="Record the stock already on your shelves when you started using KapraOS. Each variant can have exactly one opening entry."
          action={
            <Link to="/inventory/opening/new">
              <Button size="sm" leftIcon={<PackagePlus className="w-4 h-4" />}>
                Record Opening Stock
              </Button>
            </Link>
          }
        />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Posted</TableHead>
                <TableHead>Source</TableHead>
                <TableHead align="right">Lines</TableHead>
                <TableHead align="right">Estimated Costs</TableHead>
                <TableHead align="right">Total Value</TableHead>
                <TableHead>Notes</TableHead>
                <TableHead align="right" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                Array.from({ length: 4 }).map((_, i) => (
                  <TableRow key={i}>
                    <TableCell><Skeleton className="h-4 w-28" /></TableCell>
                    <TableCell><Skeleton className="h-4 w-16" /></TableCell>
                    <TableCell align="right"><Skeleton className="h-4 w-10" /></TableCell>
                    <TableCell align="right"><Skeleton className="h-4 w-12" /></TableCell>
                    <TableCell align="right"><Skeleton className="h-4 w-24" /></TableCell>
                    <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                    <TableCell align="right"><Skeleton className="h-4 w-12" /></TableCell>
                  </TableRow>
                ))
              ) : (
                batches.map((batch) => (
                  <TableRow key={batch.id}>
                    <TableCell className="whitespace-nowrap text-zinc-600">
                      {formatDate(batch.created_at)}
                    </TableCell>
                    <TableCell>
                      {batch.source === 'import' ? (
                        <Badge variant="info" size="sm">Imported</Badge>
                      ) : (
                        <Badge variant="neutral" size="sm">Manual</Badge>
                      )}
                    </TableCell>
                    <TableCell align="right" className="font-tabular">
                      {batch.entry_count}
                    </TableCell>
                    <TableCell align="right">
                      {batch.estimated_entry_count > 0 ? (
                        <Badge variant="warning" size="sm">
                          {batch.estimated_entry_count} estimated
                        </Badge>
                      ) : (
                        <span className="text-zinc-400">—</span>
                      )}
                    </TableCell>
                    <TableCell align="right" className="font-tabular font-semibold text-zinc-900">
                      {formatCurrency(Number(batch.total_value))}
                    </TableCell>
                    <TableCell className="max-w-[220px] truncate text-xs text-zinc-500" >
                      {batch.notes || '—'}
                    </TableCell>
                    <TableCell align="right">
                      <Link
                        to={`/inventory/opening/${batch.id}`}
                        className="inline-flex items-center gap-1 text-xs font-medium text-[var(--accent-ink)] dark:text-[var(--accent)] hover:underline"
                      >
                        View
                        <ArrowRight className="w-3.5 h-3.5" />
                      </Link>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </Card>
      )}
    </PageContainer>
  );
}
