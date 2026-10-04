import { SignIn } from '@clerk/clerk-react';
import { Store, ArrowRight, ShieldCheck } from 'lucide-react';
import { useNavigate, Navigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.tsx';
import { Button } from '../../components/ui/Button.tsx';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/Card.tsx';

export function LoginPage() {
  const { isClerkConfigured, isAuthenticated, isLoading, error } = useAuth();
  const navigate = useNavigate();

  if (!isLoading && isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  return (
    <div className="min-h-screen bg-[var(--clay-bg)] flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      {/* Brand Header */}
      <div className="sm:mx-auto sm:w-full sm:max-w-md text-center mb-6">
        <div className="clay-icon-tile w-10 h-10 bg-[var(--clay-primary)] text-white border-transparent rounded-[14px] mx-auto mb-3">
          <Store className="w-5 h-5" />
        </div>
        <h2 className="text-xl font-bold tracking-tight text-zinc-900 dark:text-zinc-50">
          Sign in to KapraOS
        </h2>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          Fabric & Fashion Retail Management System
        </p>
      </div>

      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        {error && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
            {error}
          </div>
        )}
        {isClerkConfigured ? (
          <div className="flex justify-center">
            <SignIn
              routing="path"
              path="/login"
              signUpUrl="/signup"
              forceRedirectUrl="/dashboard"
            />
          </div>
        ) : (
          <Card className="shadow-xs border-zinc-200">
            <CardHeader className="pb-3 border-none">
              <div className="flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                <CardTitle className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                  Development Mode
                </CardTitle>
              </div>
              <CardDescription className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
                Clerk publishable key (<code className="text-[11px] bg-zinc-100 dark:bg-zinc-800 px-1 py-0.5 rounded">VITE_CLERK_PUBLISHABLE_KEY</code>) is currently not configured in <code className="text-[11px] bg-zinc-100 dark:bg-zinc-800 px-1 py-0.5 rounded">.env</code>.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 pt-1">
              <p className="text-xs text-zinc-600 dark:text-zinc-400 leading-relaxed">
                You can proceed directly into KapraOS using the simulated store manager session to test navigation, layouts, and POS features.
              </p>
              <Button
                variant="primary"
                size="md"
                className="w-full justify-center"
                rightIcon={<ArrowRight className="w-4 h-4" />}
                onClick={() => navigate('/dashboard')}
              >
                Continue to Dashboard
              </Button>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
