import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  useMemo,
} from 'react';
import { ClerkProvider, useAuth as useClerkAuth, useUser as useClerkUser } from '@clerk/clerk-react';
import { setApiTokenGetter, ApiError } from '../lib/api/client.ts';
import { getAuthMe, syncAuth } from '../lib/api/auth.ts';
import type { CurrentUser } from '../types/index.ts';

export interface AuthContextType {
  user: CurrentUser | null;
  shopId: string | null;
  role: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  refreshAuth: () => Promise<void>;
  signOut: () => Promise<void>;
  isClerkConfigured: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const clerkPubKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || '';
const isClerkKeyValid =
  Boolean(clerkPubKey) &&
  clerkPubKey !== 'pk_test_placeholder' &&
  (clerkPubKey.startsWith('pk_test_') || clerkPubKey.startsWith('pk_live_'));

function ClerkAuthConsumer({ children }: { children: React.ReactNode }) {
  const { isSignedIn, isLoaded: isClerkLoaded, user: clerkUser } = useClerkUser();
  const { getToken, signOut: clerkSignOut } = useClerkAuth();

  const [backendUser, setBackendUser] = useState<CurrentUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setApiTokenGetter(async () => {
      try {
        return await getToken();
      } catch {
        return null;
      }
    });
  }, [getToken]);

  const loadBackendIdentity = useCallback(async () => {
    if (!isSignedIn) {
      setBackendUser(null);
      setIsLoading(false);
      return;
    }

    try {
      setIsLoading(true);
      setError(null);
      let meData;
      try {
        meData = await getAuthMe();
      } catch (err) {
        // Any 401 here means the Clerk identity has no application row yet
        // ("User not provisioned") or the token wasn't accepted. For a
        // signed-in Clerk user we attempt a one-time provision via /sync.
        // Any other status (network, 500, ...) is re-thrown below.
        if (err instanceof ApiError && err.status === 401) {
          try {
            meData = await syncAuth({
              name: clerkUser?.fullName || clerkUser?.firstName || undefined,
              email: clerkUser?.primaryEmailAddress?.emailAddress,
            });
          } catch (syncErr) {
            if (
              syncErr instanceof ApiError &&
              syncErr.status === 409
            ) {
              // Duplicate account: same email already registered to a
              // different Clerk identity. Don't fake a session -- surface
              // the message so the UI can direct the user to sign in.
              setBackendUser(null);
              setError(
                'Account already exists for this email. Kindly sign in instead.',
              );
              return;
            }
            throw syncErr;
          }
        } else {
          throw err;
        }
      }
      // Defensive: never accept a placeholder shop -- that masked the
      // tenant-reuse bug and let broken sessions into the dashboard.
      if (!meData?.shop_id || meData.shop_id === 'pending_sync') {
        throw new Error('Backend returned an invalid shop identity');
      }
      setBackendUser({
        id: meData.id,
        clerkUserId: meData.clerk_user_id,
        shopId: meData.shop_id,
        role: meData.role,
        email: clerkUser?.primaryEmailAddress?.emailAddress,
        fullName: clerkUser?.fullName || clerkUser?.firstName || 'Shop User',
      });
    } catch (err) {
      console.warn('Failed to load user identity from /auth/me:', err);
      if (err instanceof ApiError) {
        setError(err.message);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Could not connect to backend service');
      }
      // No fake fallback user: a failed identity load must not grant
      // dashboard access with a bogus shop. Callers gate on `user != null`.
      setBackendUser(null);
    } finally {
      setIsLoading(false);
    }
  }, [isSignedIn, clerkUser]);

  useEffect(() => {
    if (isClerkLoaded) {
      Promise.resolve().then(() => {
        loadBackendIdentity();
      });
    }
  }, [isClerkLoaded, loadBackendIdentity]);

  const signOut = useCallback(async () => {
    setBackendUser(null);
    await clerkSignOut();
  }, [clerkSignOut]);

  const value = useMemo<AuthContextType>(
    () => ({
      user: backendUser,
      shopId: backendUser?.shopId || null,
      role: backendUser?.role || null,
      // Require BOTH Clerk sign-in AND a provisioned backend identity.
      // Previously `isSignedIn` alone granted access, so a failed /sync
      // (or a duplicate-account 409) still let users into the dashboard
      // with a fake `pending_sync` shop.
      isAuthenticated: Boolean(isSignedIn && backendUser),
      isLoading: !isClerkLoaded || isLoading,
      error,
      refreshAuth: loadBackendIdentity,
      signOut,
      isClerkConfigured: true,
    }),
    [backendUser, isSignedIn, isClerkLoaded, isLoading, error, loadBackendIdentity, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

function FallbackAuthProvider({ children }: { children: React.ReactNode }) {
  const [mockUser, setMockUser] = useState<CurrentUser | null>({
    id: 'usr_local_dev',
    clerkUserId: 'user_dev_local',
    shopId: 'shop_default_local',
    role: 'owner',
    email: 'dev@kapraos.local',
    fullName: 'KapraOS Store Manager',
  });

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshAuth = useCallback(async () => {
    try {
      setIsLoading(true);
      const meData = await getAuthMe();
      setMockUser({
        id: meData.id,
        clerkUserId: meData.clerk_user_id,
        shopId: meData.shop_id,
        role: meData.role,
        email: 'shop@kapraos.local',
        fullName: 'Store Admin',
      });
      setError(null);
    } catch {
      // Backend not reachable or unauthenticated
    } finally {
      setIsLoading(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    setMockUser(null);
  }, []);

  const value = useMemo<AuthContextType>(
    () => ({
      user: mockUser,
      shopId: mockUser?.shopId || null,
      role: mockUser?.role || null,
      isAuthenticated: Boolean(mockUser),
      isLoading,
      error,
      refreshAuth,
      signOut,
      isClerkConfigured: false,
    }),
    [mockUser, isLoading, error, refreshAuth, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  if (isClerkKeyValid) {
    return (
      <ClerkProvider publishableKey={clerkPubKey}>
        <ClerkAuthConsumer>{children}</ClerkAuthConsumer>
      </ClerkProvider>
    );
  }

  return <FallbackAuthProvider>{children}</FallbackAuthProvider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}

