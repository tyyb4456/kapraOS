import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Search, ChevronLeft, ChevronRight } from 'lucide-react';
import { PageContainer, PageHeader } from '../../components/layout/index.ts';
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
  Skeleton,
  Alert,
} from '../../components/ui/index.ts';
import { getAdminShops } from '../../lib/api/admin.ts';
import { formatDate } from '../../lib/formatters.ts';
import type { AdminShopListResponse } from '../../types/index.ts';

const PAGE_SIZE = 25;

export function AdminShopsPage() {
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<AdminShopListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<number | null>(null);

  const handleSearchChange = (value: string) => {
    setSearchInput(value);
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      setSearch(value.trim());
      setOffset(0);
    }, 300);
  };

  useEffect(
    () => () => {
      if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    },
    [],
  );

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await getAdminShops({
        search: search || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load platform shops');
    } finally {
      setLoading(false);
    }
  }, [search, offset]);

  useEffect(() => {
    load();
  }, [load]);

  const total = data?.total ?? 0;
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + PAGE_SIZE, total);
  const canPrevious = offset > 0;
  const canNext = offset + PAGE_SIZE < total;

  return (
    <PageContainer>
      <PageHeader
        title="Platform Shops"
        description="Every shop tenant on KapraOS with owner and aggregate activity counts."
        breadcrumbs={[
          { label: 'Platform', href: '/admin' },
          { label: 'Shops' },
        ]}
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4">
          <Input
            placeholder="Search shops by name, phone, or address..."
            value={searchInput}
            onChange={(e) => handleSearchChange(e.target.value)}
            leftIcon={<Search className="w-4 h-4" />}
          />
        </CardContent>
      </Card>

      {error && (
        <Alert variant="danger" title="Could not load shops">
          {error}
        </Alert>
      )}

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Shop</TableHead>
              <TableHead>Owner</TableHead>
              <TableHead align="right">Members</TableHead>
              <TableHead align="right">Sales</TableHead>
              <TableHead align="right">Purchases</TableHead>
              <TableHead align="right">Products</TableHead>
              <TableHead>Created</TableHead>
              <TableHead align="right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 6 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton className="h-4 w-40" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-10" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-10" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-10" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-10" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-12" /></TableCell>
                </TableRow>
              ))
            ) : !data || data.shops.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center py-8 text-zinc-500">
                  {search ? 'No shops match the current search' : 'No shops registered yet'}
                </TableCell>
              </TableRow>
            ) : (
              data.shops.map((shop) => (
                <TableRow key={shop.id}>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-medium text-zinc-900 dark:text-zinc-100">
                        {shop.name}
                      </span>
                      {shop.phone && (
                        <span className="text-xs text-zinc-500 dark:text-zinc-400 font-mono">
                          {shop.phone}
                        </span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    {shop.owner ? (
                      <div className="flex flex-col">
                        <span className="text-xs text-zinc-800 dark:text-zinc-200">
                          {shop.owner.name}
                        </span>
                        <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                          {shop.owner.email}
                        </span>
                      </div>
                    ) : (
                      <span className="text-xs text-zinc-400 dark:text-zinc-500">No owner</span>
                    )}
                  </TableCell>
                  <TableCell align="right" className="font-tabular text-zinc-700 dark:text-zinc-300">
                    {shop.user_count}
                  </TableCell>
                  <TableCell align="right" className="font-tabular text-zinc-700 dark:text-zinc-300">
                    {shop.sales_count}
                  </TableCell>
                  <TableCell align="right" className="font-tabular text-zinc-700 dark:text-zinc-300">
                    {shop.purchases_count}
                  </TableCell>
                  <TableCell align="right" className="font-tabular text-zinc-700 dark:text-zinc-300">
                    {shop.products_count}
                  </TableCell>
                  <TableCell className="text-xs text-zinc-600 dark:text-zinc-400 font-tabular">
                    {formatDate(shop.created_at)}
                  </TableCell>
                  <TableCell align="right">
                    <Link to={`/admin/shops/${shop.id}`}>
                      <Button variant="ghost" size="sm" className="h-7 text-xs px-2">
                        View
                      </Button>
                    </Link>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      <div className="flex items-center justify-between">
        <p className="text-xs text-zinc-500 dark:text-zinc-400 font-tabular">
          {total === 0 ? 'No shops' : `Showing ${rangeStart}–${rangeEnd} of ${total} shops`}
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!canPrevious || loading}
            onClick={() => setOffset((prev) => Math.max(0, prev - PAGE_SIZE))}
            leftIcon={<ChevronLeft className="w-4 h-4" />}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!canNext || loading}
            onClick={() => setOffset((prev) => prev + PAGE_SIZE)}
            rightIcon={<ChevronRight className="w-4 h-4" />}
          >
            Next
          </Button>
        </div>
      </div>
    </PageContainer>
  );
}
