import { useEffect, useState } from 'react';
import { Activity, Info, RefreshCw } from 'lucide-react';
import { PageContainer, PageHeader } from '../../components/layout/index.ts';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
  Badge,
  Skeleton,
  Alert,
  EmptyState,
  Button,
} from '../../components/ui/index.ts';
import { getAdminHealth } from '../../lib/api/admin.ts';
import { formatDate } from '../../lib/formatters.ts';
import type { AdminHealth } from '../../types/index.ts';

export function AdminHealthPage() {
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await getAdminHealth();
        if (!cancelled) setHealth(data);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load platform health');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const overall = health?.status ?? 'unknown';

  return (
    <PageContainer>
      <PageHeader
        title="Platform Health"
        description="Live dependency checks for the KapraOS platform: database, cache and services."
        breadcrumbs={[{ label: 'Platform' }, { label: 'Health' }]}
        actions={
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<RefreshCw className="w-4 h-4" />}
            isLoading={loading}
            onClick={() => setRefreshKey((key) => key + 1)}
          >
            Refresh
          </Button>
        }
      />

      {error && (
        <Alert variant="danger" title="Could not load platform health">
          {error}
        </Alert>
      )}

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div>
              <CardTitle>Dependency Checks</CardTitle>
              <CardDescription>
                {health
                  ? `Checked ${formatDate(health.checked_at, true)}`
                  : 'Live probes against every platform dependency.'}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-zinc-500 dark:text-zinc-400">Overall</span>
              <Badge variant={overall === 'ok' ? 'success' : 'warning'} size="sm">
                <Activity className="w-3 h-3" />
                {overall}
              </Badge>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-4">
          {loading && !health ? (
            <div className="space-y-2">
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-4 w-64" />
              <Skeleton className="h-4 w-40" />
            </div>
          ) : health ? (
            <div className="divide-y divide-[var(--clay-border-soft)]">
              {health.checks.map((check) => (
                <div key={check.name} className="flex items-center justify-between py-2.5 gap-3">
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-zinc-800 dark:text-zinc-200">
                      {check.name}
                    </p>
                    {check.detail && (
                      <p className="text-[11px] text-zinc-500 dark:text-zinc-400 truncate">
                        {check.detail}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {check.latency_ms !== null && (
                      <span className="text-[11px] font-tabular text-zinc-500 dark:text-zinc-400">
                        {check.latency_ms.toFixed(1)} ms
                      </span>
                    )}
                    <Badge variant={check.status === 'ok' ? 'success' : 'destructive'} size="sm">
                      {check.status}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<Info className="w-5 h-5" />}
              title="Health unavailable"
              description="The platform health endpoint could not be reached."
            />
          )}
        </CardContent>
      </Card>
    </PageContainer>
  );
}
