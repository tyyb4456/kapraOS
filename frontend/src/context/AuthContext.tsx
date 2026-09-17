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
import { getAuthMe } from '../lib/api/auth.ts';
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
      const meData = await getAuthMe();
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
      } else {
        setError('Could not connect to backend service');
      }
      if (clerkUser) {
        setBackendUser({
          id: clerkUser.id,
          clerkUserId: clerkUser.id,
          shopId: 'pending_sync',
          role: 'owner',
          email: clerkUser.primaryEmailAddress?.emailAddress,
          fullName: clerkUser.fullName || 'Shop User',
        });
      }
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
      isAuthenticated: Boolean(isSignedIn),
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

