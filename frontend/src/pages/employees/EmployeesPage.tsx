import { useCallback, useEffect, useState } from 'react';
import { Check, Copy, Mail, Trash2, UserCog, UserPlus } from 'lucide-react';
import { PageContainer, PageHeader } from '../../components/layout/index.ts';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Select,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../../components/ui/index.ts';
import {
  createInvitation,
  listEmployees,
  listInvitations,
  revokeEmployee,
  revokeInvitation,
} from '../../lib/api/employees.ts';
import type {
  EmployeeInvitation,
  InvitationStatus,
  ShopEmployee,
} from '../../types/index.ts';

const statusVariant: Record<InvitationStatus, 'warning' | 'success' | 'neutral' | 'danger'> = {
  pending: 'warning',
  accepted: 'success',
  revoked: 'neutral',
  expired: 'danger',
};

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function absoluteInviteUrl(url: string): string {
  if (!url) return '';
  if (url.startsWith('/')) return `${window.location.origin}${url}`;
  return url;
}

export function EmployeesPage() {
  const [employees, setEmployees] = useState<ShopEmployee[]>([]);
  const [invitations, setInvitations] = useState<EmployeeInvitation[]>([]);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<'cashier' | 'salesman'>('cashier');
  const [inviteLink, setInviteLink] = useState('');
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const [employeeRows, invitationRows] = await Promise.all([
      listEmployees(),
      listInvitations(),
    ]);
    setEmployees(employeeRows);
    setInvitations(invitationRows);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        await reload();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load employees');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reload]);

  const handleInvite = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!email.trim()) {
      setError('Enter the employee email address to invite.');
      return;
    }
    setCreating(true);
    setError(null);
    setInviteLink('');
    setCopied(false);
    try {
      const created = await createInvitation({ email: email.trim(), role });
      setInviteLink(absoluteInviteUrl(created.invite_url || created.invite_path));
      setEmail('');
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create invitation');
    } finally {
      setCreating(false);
    }
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(inviteLink);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setError('Could not copy automatically; select the link and copy it manually.');
    }
  };

  const handleRevokeInvitation = async (invitation: EmployeeInvitation) => {
    if (!window.confirm(`Revoke the invitation for ${invitation.email}?`)) return;
    setBusyId(invitation.id);
    setError(null);
    try {
      await revokeInvitation(invitation.id);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke invitation');
    } finally {
      setBusyId(null);
    }
  };

  const handleRevokeEmployee = async (employee: ShopEmployee) => {
    if (
      !window.confirm(
        `Remove ${employee.name}'s access to this shop? They will be signed out of shop data immediately.`,
      )
    ) {
      return;
    }
    setBusyId(employee.id);
    setError(null);
    try {
      await revokeEmployee(employee.id);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke employee');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <PageContainer>
      <PageHeader
        title="Employees & Invitations"
        description="Invite salesmen into your shop and control who can access the register."
        breadcrumbs={[{ label: 'Team' }, { label: 'Employees' }]}
      />

      {error && (
        <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-700 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-200">
          {error}
        </div>
      )}

      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center gap-2">
            <UserPlus className="w-4 h-4 text-[var(--clay-primary-active)]" />
            <CardTitle className="text-sm font-semibold">Invite an employee</CardTitle>
          </div>
          <CardDescription className="text-xs">
            The invitee must sign up with this exact email address. The link is
            single-use and expires in 7 days.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleInvite} className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1">
              <Input
                label="Employee email"
                type="email"
                placeholder="salesman@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                leftIcon={<Mail className="w-4 h-4" />}
              />
            </div>
            <div className="w-full sm:w-48">
              <Select
                label="Role"
                value={role}
                onChange={(e) => setRole(e.target.value as 'cashier' | 'salesman')}
                options={[
                  { value: 'cashier', label: 'Cashier (Salesman)' },
                  { value: 'salesman', label: 'Salesman' },
                ]}
              />
            </div>
            <Button type="submit" variant="primary" size="md" isLoading={creating}>
              Create invite
            </Button>
          </form>

          {inviteLink && (
            <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-3 dark:border-emerald-800 dark:bg-emerald-950/40">
              <p className="text-xs font-medium text-emerald-800 dark:text-emerald-200">
                Invitation link created. Share it with the employee over WhatsApp
                or email — KapraOS does not send emails itself.
              </p>
              <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
                <code className="min-w-0 flex-1 break-all rounded-md bg-white px-2 py-1.5 text-[11px] text-zinc-700 dark:bg-zinc-900 dark:text-zinc-200">
                  {inviteLink}
                </code>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  leftIcon={
                    copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />
                  }
                  onClick={handleCopy}
                >
                  {copied ? 'Copied' : 'Copy link'}
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <UserCog className="w-4 h-4 text-zinc-500" />
              <CardTitle className="text-sm font-semibold">Current employees</CardTitle>
            </div>
            <CardDescription className="text-xs">
              Salesmen see the POS and their own sales only.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Employee</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead align="right">Joined</TableHead>
                  <TableHead align="right">Access</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  Array.from({ length: 2 }).map((_, i) => (
                    <TableRow key={i}>
                      <TableCell><Skeleton className="h-4 w-40" /></TableCell>
                      <TableCell><Skeleton className="h-4 w-16" /></TableCell>
                      <TableCell align="right"><Skeleton className="h-4 w-20" /></TableCell>
                      <TableCell align="right"><Skeleton className="h-4 w-16" /></TableCell>
                    </TableRow>
                  ))
                ) : employees.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={4} className="py-8 text-center text-zinc-500">
                      No employees yet. Invite a salesman above.
                    </TableCell>
                  </TableRow>
                ) : (
                  employees.map((employee) => (
                    <TableRow key={employee.id}>
                      <TableCell>
                        <div className="font-medium text-zinc-900 dark:text-zinc-100">
                          {employee.name}
                        </div>
                        <div className="text-[11px] text-zinc-500">{employee.email}</div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="neutral" size="sm" className="capitalize">
                          {employee.role}
                        </Badge>
                      </TableCell>
                      <TableCell align="right" className="text-xs text-zinc-500">
                        {formatDate(employee.created_at)}
                      </TableCell>
                      <TableCell align="right">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs text-rose-600 hover:bg-rose-50"
                          leftIcon={<Trash2 className="w-3.5 h-3.5" />}
                          disabled={busyId === employee.id}
                          onClick={() => handleRevokeEmployee(employee)}
                        >
                          Remove
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <Mail className="w-4 h-4 text-zinc-500" />
              <CardTitle className="text-sm font-semibold">Invitations</CardTitle>
            </div>
            <CardDescription className="text-xs">
              Pending invites can be revoked before they are accepted.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead align="right">Expires</TableHead>
                  <TableHead align="right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  Array.from({ length: 2 }).map((_, i) => (
                    <TableRow key={i}>
                      <TableCell><Skeleton className="h-4 w-40" /></TableCell>
                      <TableCell><Skeleton className="h-4 w-16" /></TableCell>
                      <TableCell align="right"><Skeleton className="h-4 w-20" /></TableCell>
                      <TableCell align="right"><Skeleton className="h-4 w-16" /></TableCell>
                    </TableRow>
                  ))
                ) : invitations.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={4} className="py-8 text-center text-zinc-500">
                      No invitations sent yet.
                    </TableCell>
                  </TableRow>
                ) : (
                  invitations.map((invitation) => (
                    <TableRow key={invitation.id}>
                      <TableCell>
                        <div className="font-medium text-zinc-900 dark:text-zinc-100">
                          {invitation.email}
                        </div>
                        <div className="text-[11px] capitalize text-zinc-500">
                          {invitation.role}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusVariant[invitation.status]} size="sm" className="capitalize">
                          {invitation.status}
                        </Badge>
                      </TableCell>
                      <TableCell align="right" className="text-xs text-zinc-500">
                        {formatDate(invitation.expires_at)}
                      </TableCell>
                      <TableCell align="right">
                        {invitation.status === 'pending' ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 text-xs text-rose-600 hover:bg-rose-50"
                            disabled={busyId === invitation.id}
                            onClick={() => handleRevokeInvitation(invitation)}
                          >
                            Revoke
                          </Button>
                        ) : (
                          <span className="text-[11px] text-zinc-400">—</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}
