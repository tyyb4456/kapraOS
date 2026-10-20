import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Search, ShieldCheck, ChevronLeft, ChevronRight } from 'lucide-react';
import { PageContainer, PageHeader } from '../../components/layout/index.ts';
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
  Badge,
  Skeleton,
  Alert,
} from '../../components/ui/index.ts';
import { getAdminUsers } from '../../lib/api/admin.ts';
import { formatDate } from '../../lib/formatters.ts';
import type { AdminUserListResponse } from '../../types/index.ts';

const PAGE_SIZE = 25;

export function AdminUsersPage() {
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [role, setRole] = useState<'' | 'owner' | 'staff'>('');
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<AdminUserListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<number | null>(null);

  // Debounce free-text search so typing doesn't fire a request per keypress.
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
      const result = await getAdminUsers({
        search: search || undefined,
        role: role || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load platform users');
    } finally {
      setLoading(false);
    }
  }, [search, role, offset]);

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
        title="Platform Users"
        description="Every registered user across all shops. Each user belongs to exactly one shop."
        breadcrumbs={[
          { label: 'Platform', href: '/admin' },
          { label: 'Users' },
        ]}
      />

      <Card>
        <CardContent className="p-3.5 sm:p-4 flex flex-col sm:flex-row gap-3">
          <div className="flex-1">
            <Input
              placeholder="Search users by name, email, or Clerk ID..."
              value={searchInput}
              onChange={(e) => handleSearchChange(e.target.value)}
              leftIcon={<Search className="w-4 h-4" />}
            />
          </div>
          <div className="w-full sm:w-44">
            <Select
              value={role}
              onChange={(e) => {
                setRole(e.target.value as '' | 'owner' | 'staff');
                setOffset(0);
              }}
              options={[
                { value: '', label: 'All roles' },
                { value: 'owner', label: 'Owners' },
                { value: 'staff', label: 'Staff' },
              ]}
            />
          </div>
        </CardContent>
      </Card>

      {error && (
        <Alert variant="danger" title="Could not load users">
          {error}
        </Alert>
      )}

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>User</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Shop</TableHead>
              <TableHead>Platform Admin</TableHead>
              <TableHead>Joined</TableHead>
              <TableHead align="right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 6 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton className="h-4 w-48" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-16" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-16" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell align="right"><Skeleton className="h-4 w-12" /></TableCell>
                </TableRow>
              ))
            ) : !data || data.users.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center py-8 text-zinc-500">
                  {search || role ? 'No users match the current filters' : 'No users registered yet'}
                </TableCell>
              </TableRow>
            ) : (
              data.users.map((user) => (
                <TableRow key={user.id}>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-medium text-zinc-900 dark:text-zinc-100">
                        {user.name}
                      </span>
                      <span className="text-xs text-zinc-500 dark:text-zinc-400">
                        {user.email}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge variant={user.role === 'owner' ? 'brand' : 'neutral'} size="sm">
                      {user.role}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {user.shop_id && user.shop_name ? (
                      <Link
                        to={`/admin/shops/${user.shop_id}`}
                        className="text-xs text-[var(--accent-ink)] dark:text-[var(--accent)] hover:underline"
                      >
                        {user.shop_name}
                      </Link>
                    ) : (
                      <span className="text-xs text-zinc-400 dark:text-zinc-500">No shop</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {user.is_platform_admin ? (
                      <Badge variant="warning" size="sm">
                        <ShieldCheck className="w-3 h-3" />
                        Admin
                      </Badge>
                    ) : (
                      <span className="text-xs text-zinc-400 dark:text-zinc-500">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-zinc-600 dark:text-zinc-400 font-tabular">
                    {formatDate(user.created_at)}
                  </TableCell>
                  <TableCell align="right">
                    <Link to={`/admin/users/${user.id}`}>
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
          {total === 0 ? 'No users' : `Showing ${rangeStart}–${rangeEnd} of ${total} users`}
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
