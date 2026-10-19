import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, ShieldCheck } from 'lucide-react';
import { PageContainer, PageHeader } from '../../components/layout/index.ts';
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
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
import { getAdminShop } from '../../lib/api/admin.ts';
import { formatDate } from '../../lib/formatters.ts';
import type { AdminShopDetailResponse } from '../../types/index.ts';

function CountTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="clay-panel-soft p-3 text-center">
      <p className="text-lg font-bold font-tabular text-zinc-900 dark:text-zinc-50">{value}</p>
      <p className="text-[11px] text-zinc-500 dark:text-zinc-400">{label}</p>
    </div>
  );
}

export function AdminShopDetailPage() {
  const { shopId } = useParams<{ shopId: string }>();
  const [data, setData] = useState<AdminShopDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      if (!shopId) return;
      try {
        setLoading(true);
        setError(null);
        const result = await getAdminShop(shopId);
        if (!cancelled) setData(result);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load shop');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [shopId]);

  return (
    <PageContainer>
      <PageHeader
        title={data ? data.shop.name : 'Shop Detail'}
        description="Aggregate tenant activity. Individual business records (sales, purchases, khatas) are not exposed to the platform panel."
        breadcrumbs={[
          { label: 'Platform', href: '/admin' },
          { label: 'Shops', href: '/admin/shops' },
          { label: 'Detail' },
        ]}
        actions={
          <Link to="/admin/shops">
            <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-4 h-4" />}>
              Back to Shops
            </Button>
          </Link>
        }
      />

      {error && (
        <Alert variant="danger" title="Could not load shop">
          {error}
        </Alert>
      )}

      {loading ? (
        <div className="space-y-4">
          <Card>
            <CardContent className="p-5 space-y-3">
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-4 w-64" />
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-5 space-y-3">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-4 w-56" />
            </CardContent>
          </Card>
        </div>
      ) : data ? (
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <CountTile label="Members" value={data.shop.user_count} />
            <CountTile label="Sales" value={data.shop.sales_count} />
            <CountTile label="Purchases" value={data.shop.purchases_count} />
            <CountTile label="Products" value={data.shop.products_count} />
            <CountTile label="Customers" value={data.customers_count} />
            <CountTile label="Suppliers" value={data.suppliers_count} />
            <CountTile label="Expenses" value={data.expenses_count} />
            <CountTile
              label="Last Sale"
              value={data.last_sale_at ? formatDate(data.last_sale_at) : '—'}
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Shop Profile</CardTitle>
              <CardDescription>Tenant metadata and owner identity.</CardDescription>
            </CardHeader>
            <CardContent className="divide-y divide-[var(--clay-border-soft)]">
              <div className="flex items-start justify-between gap-4 py-2">
                <span className="text-xs text-zinc-500 dark:text-zinc-400">Shop ID</span>
                <span className="text-xs text-zinc-900 dark:text-zinc-100 break-all">
                  {data.shop.id}
                </span>
              </div>
              <div className="flex items-start justify-between gap-4 py-2">
                <span className="text-xs text-zinc-500 dark:text-zinc-400">Owner</span>
                <span className="text-xs text-zinc-900 dark:text-zinc-100 text-right">
                  {data.shop.owner ? (
                    <>
                      {data.shop.owner.name}
                      <br />
                      <span className="text-zinc-500 dark:text-zinc-400">
                        {data.shop.owner.email}
                      </span>
                    </>
                  ) : (
                    'No owner registered'
                  )}
                </span>
              </div>
              <div className="flex items-start justify-between gap-4 py-2">
                <span className="text-xs text-zinc-500 dark:text-zinc-400">Phone</span>
                <span className="text-xs text-zinc-900 dark:text-zinc-100 font-mono">
                  {data.shop.phone || '—'}
                </span>
              </div>
              <div className="flex items-start justify-between gap-4 py-2">
                <span className="text-xs text-zinc-500 dark:text-zinc-400">Address</span>
                <span className="text-xs text-zinc-900 dark:text-zinc-100 text-right">
                  {data.shop.address || '—'}
                </span>
              </div>
              <div className="flex items-start justify-between gap-4 py-2">
                <span className="text-xs text-zinc-500 dark:text-zinc-400">Currency</span>
                <span className="text-xs text-zinc-900 dark:text-zinc-100">
                  {data.shop.currency}
                </span>
              </div>
              <div className="flex items-start justify-between gap-4 py-2">
                <span className="text-xs text-zinc-500 dark:text-zinc-400">Created</span>
                <span className="text-xs text-zinc-900 dark:text-zinc-100">
                  {formatDate(data.shop.created_at, true)}
                </span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Members</CardTitle>
              <CardDescription>Users belonging to this shop.</CardDescription>
            </CardHeader>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Platform Admin</TableHead>
                  <TableHead>Joined</TableHead>
                  <TableHead align="right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.members.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center py-8 text-zinc-500">
                      No members in this shop
                    </TableCell>
                  </TableRow>
                ) : (
                  data.members.map((member) => (
                    <TableRow key={member.id}>
                      <TableCell className="font-medium text-zinc-900 dark:text-zinc-100">
                        {member.name}
                      </TableCell>
                      <TableCell className="text-xs text-zinc-600 dark:text-zinc-400">
                        {member.email}
                      </TableCell>
                      <TableCell>
                        <Badge variant={member.role === 'owner' ? 'brand' : 'neutral'} size="sm">
                          {member.role}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {member.is_platform_admin ? (
                          <Badge variant="warning" size="sm">
                            <ShieldCheck className="w-3 h-3" />
                            Admin
                          </Badge>
                        ) : (
                          <span className="text-xs text-zinc-400 dark:text-zinc-500">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-zinc-600 dark:text-zinc-400 font-tabular">
                        {formatDate(member.created_at)}
                      </TableCell>
                      <TableCell align="right">
                        <Link to={`/admin/users/${member.id}`}>
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
        </div>
      ) : null}
    </PageContainer>
  );
}
