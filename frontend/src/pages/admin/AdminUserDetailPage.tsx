import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, ShieldCheck, Store, Users } from 'lucide-react';
import { PageContainer, PageHeader } from '../../components/layout/index.ts';
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
  Badge,
  Skeleton,
  Alert,
} from '../../components/ui/index.ts';
import { getAdminUser } from '../../lib/api/admin.ts';
import { formatDate } from '../../lib/formatters.ts';
import type { AdminUserDetailResponse } from '../../types/index.ts';

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2">
      <span className="text-xs text-zinc-500 dark:text-zinc-400 shrink-0">{label}</span>
      <span className="text-xs text-zinc-900 dark:text-zinc-100 text-right break-all">
        {children}
      </span>
    </div>
  );
}

export function AdminUserDetailPage() {
  const { userId } = useParams<{ userId: string }>();
  const [data, setData] = useState<AdminUserDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      if (!userId) return;
      try {
        setLoading(true);
        setError(null);
        const result = await getAdminUser(userId);
        if (!cancelled) setData(result);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load user');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [userId]);

  return (
    <PageContainer maxWidth="narrow">
      <PageHeader
        title={data ? data.user.name : 'User Detail'}
        description="Platform view of one user and their shop relationship."
        breadcrumbs={[
          { label: 'Platform', href: '/admin' },
          { label: 'Users', href: '/admin/users' },
          { label: 'Detail' },
        ]}
        actions={
          <Link to="/admin/users">
            <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-4 h-4" />}>
              Back to Users
            </Button>
          </Link>
        }
      />

      {error && (
        <Alert variant="danger" title="Could not load user">
          {error}
        </Alert>
      )}

      {loading ? (
        <div className="space-y-4">
          <Card>
            <CardContent className="p-5 space-y-3">
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-4 w-64" />
              <Skeleton className="h-4 w-40" />
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
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-3">
                <CardTitle>Account</CardTitle>
                <div className="flex items-center gap-1.5">
                  <Badge variant={data.user.role === 'owner' ? 'brand' : 'neutral'} size="sm">
                    {data.user.role}
                  </Badge>
                  {data.user.is_platform_admin && (
                    <Badge variant="warning" size="sm">
                      <ShieldCheck className="w-3 h-3" />
                      Platform Admin
                    </Badge>
                  )}
                </div>
              </div>
              <CardDescription>Identity and registration metadata.</CardDescription>
            </CardHeader>
            <CardContent className="divide-y divide-[var(--clay-border-soft)]">
              <DetailRow label="Name">{data.user.name}</DetailRow>
              <DetailRow label="Email">{data.user.email}</DetailRow>
              <DetailRow label="Clerk User ID">{data.user.clerk_user_id || '—'}</DetailRow>
              <DetailRow label="User ID">{data.user.id}</DetailRow>
              <DetailRow label="Joined">{formatDate(data.user.created_at, true)}</DetailRow>
              <DetailRow label="Last Updated">{formatDate(data.user.updated_at, true)}</DetailRow>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Shop</CardTitle>
              <CardDescription>
                This user's single shop membership on the platform.
              </CardDescription>
            </CardHeader>
            <CardContent className="divide-y divide-[var(--clay-border-soft)]">
              <DetailRow label="Shop Name">
                <Link
                  to={`/admin/shops/${data.shop.id}`}
                  className="text-[var(--clay-primary-active)] hover:underline inline-flex items-center gap-1.5"
                >
                  <Store className="w-3.5 h-3.5" />
                  {data.shop.name}
                </Link>
              </DetailRow>
              <DetailRow label="Shop ID">{data.shop.id}</DetailRow>
              <DetailRow label="Currency">{data.shop.currency}</DetailRow>
              <DetailRow label="Shop Created">{formatDate(data.shop.created_at)}</DetailRow>
              <DetailRow label="Members in Shop">
                <span className="inline-flex items-center gap-1.5">
                  <Users className="w-3.5 h-3.5 text-zinc-400" />
                  {data.shop_members_count}
                </span>
              </DetailRow>
            </CardContent>
          </Card>
        </div>
      ) : null}
    </PageContainer>
  );
}
