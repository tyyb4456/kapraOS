import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowRight, CheckCircle2, Loader2, Store, XCircle } from 'lucide-react';
import { useAuth } from '../../context/AuthContext.tsx';
import { acceptInvitation } from '../../lib/api/employees.ts';
import {
  clearPendingInviteToken,
  rememberInviteToken,
} from '../../lib/inviteToken.ts';
import { Button } from '../../components/ui/Button.tsx';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '../../components/ui/Card.tsx';
import type { AcceptInvitationResult } from '../../types/index.ts';

type InviteState = 'waiting' | 'accepting' | 'success' | 'error';

export function InviteAcceptPage() {
  const { token = '' } = useParams<{ token: string }>();
  const navigate = useNavigate();
  const {
    isLoading,
    isSignedIn,
    isClerkConfigured,
    error: authError,
    refreshAuth,
    signOut,
  } = useAuth();

  const [state, setState] = useState<InviteState>('waiting');
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AcceptInvitationResult | null>(null);
  const attempted = useRef(false);

  const invitePath = `/invite/${token}`;
  const signUpHref = `/signup?redirect_url=${encodeURIComponent(invitePath)}`;
  const signInHref = `/login?redirect_url=${encodeURIComponent(invitePath)}`;

  useEffect(() => {
    if (token) rememberInviteToken(token);
  }, [token]);

  useEffect(() => {
    if (isLoading || !token || !isClerkConfigured || !isSignedIn) return;
    if (attempted.current) return;
    attempted.current = true;

    let cancelled = false;
    (async () => {
      setState('accepting');
      setError(null);
      try {
        const accepted = await acceptInvitation(token);
        if (cancelled) return;
        setResult(accepted);
        clearPendingInviteToken();
        setState('success');
        await refreshAuth();
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : 'Could not accept this invitation',
        );
        setState('error');
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [isLoading, isSignedIn, isClerkConfigured, token, refreshAuth]);

  const clerkMissing = !isLoading && !isClerkConfigured;
  const displayError =
    error ??
    authError ??
    (clerkMissing
      ? 'Invitations require Clerk sign-in, which is not configured in this environment.'
      : null);

  const retry = () => {
    attempted.current = false;
    setState('waiting');
    setError(null);
    // Re-run the effect by flipping a state-dependent value: simplest is a
    // full reload of the invite route.
    window.location.reload();
  };

  return (
    <div className="min-h-screen bg-[var(--clay-bg)] flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md text-center mb-6">
        <div className="clay-icon-tile w-10 h-10 bg-[var(--clay-primary)] text-white border-transparent rounded-[14px] mx-auto mb-3">
          <Store className="w-5 h-5" />
        </div>
        <h2 className="text-xl font-bold tracking-tight text-zinc-900 dark:text-zinc-50">
          Join your shop on KapraOS
        </h2>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          Employee invitation
        </p>
      </div>

      <div className="sm:mx-auto sm:w-full sm:max-w-md space-y-4">
        {!clerkMissing &&
          (state === 'accepting' || isLoading || (state === 'waiting' && isSignedIn)) && (
          <Card className="shadow-xs border-zinc-200">
            <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
              <Loader2 className="w-5 h-5 animate-spin text-[var(--clay-primary-active)]" />
              <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                {isLoading ? 'Checking your session…' : 'Accepting invitation…'}
              </p>
              {!isLoading && isSignedIn && (
                <p className="text-xs text-zinc-500">This only takes a moment.</p>
              )}
            </CardContent>
          </Card>
        )}

        {!isLoading && !isSignedIn && isClerkConfigured && (
          <Card className="shadow-xs border-zinc-200">
            <CardHeader className="pb-3 border-none">
              <CardTitle className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Sign in to accept
              </CardTitle>
              <CardDescription className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
                Use the email address this invitation was sent to. If you do not
                have an account yet, create one — you will be joined to the
                shop automatically.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 pt-1">
              <Button
                variant="primary"
                size="md"
                className="w-full justify-center"
                rightIcon={<ArrowRight className="w-4 h-4" />}
                onClick={() => navigate(signUpHref)}
              >
                Create an account
              </Button>
              <Button
                variant="outline"
                size="md"
                className="w-full justify-center"
                onClick={() => navigate(signInHref)}
              >
                I already have an account
              </Button>
            </CardContent>
          </Card>
        )}

        {state === 'success' && (
          <Card className="shadow-xs border-emerald-200 dark:border-emerald-800">
            <CardContent className="flex flex-col items-center gap-3 py-8 text-center">
              <CheckCircle2 className="w-8 h-8 text-emerald-600 dark:text-emerald-400" />
              <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                You have joined {result?.shop_name || 'the shop'}
              </p>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Your role: <span className="font-medium capitalize">{result?.role || 'cashier'}</span>
                {result?.already_member ? ' · already a member' : ''}
              </p>
              <Button
                variant="primary"
                size="md"
                className="mt-1 w-full justify-center"
                rightIcon={<ArrowRight className="w-4 h-4" />}
                onClick={() => navigate('/dashboard')}
              >
                Start selling
              </Button>
            </CardContent>
          </Card>
        )}

        {(state === 'error' || clerkMissing) && (
          <Card className="shadow-xs border-rose-200 dark:border-rose-800">
            <CardContent className="flex flex-col items-center gap-3 py-8 text-center">
              <XCircle className="w-8 h-8 text-rose-600 dark:text-rose-400" />
              <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                We could not accept this invitation
              </p>
              <p className="text-xs text-zinc-600 dark:text-zinc-400">
                {displayError || 'The invitation link is invalid or has expired.'}
              </p>
              <div className="mt-1 flex w-full flex-col gap-2">
                {isSignedIn && (
                  <Button
                    variant="outline"
                    size="md"
                    className="w-full justify-center"
                    onClick={retry}
                  >
                    Try again
                  </Button>
                )}
                {isSignedIn ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-full justify-center"
                    onClick={() => signOut()}
                  >
                    Sign out and use a different account
                  </Button>
                ) : (
                  <Link to="/login" className="text-xs font-medium text-zinc-500 underline">
                    Go to sign in
                  </Link>
                )}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
