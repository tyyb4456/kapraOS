const PENDING_INVITE_KEY = 'kapraos.pending_invite_token';

/**
 * Persist an invitation token across the Clerk sign-up redirect so the next
 * `/auth/sync` can redeem it. sessionStorage keeps the token scoped to this
 * tab and it is cleared as soon as the backend accepts the invitation.
 */
export function rememberInviteToken(token: string): void {
  try {
    sessionStorage.setItem(PENDING_INVITE_KEY, token);
  } catch {
    // Storage unavailable (private mode); the invite page can still retry.
  }
}

export function getPendingInviteToken(): string | null {
  try {
    return sessionStorage.getItem(PENDING_INVITE_KEY);
  } catch {
    return null;
  }
}

export function clearPendingInviteToken(): void {
  try {
    sessionStorage.removeItem(PENDING_INVITE_KEY);
  } catch {
    // Nothing to clear.
  }
}
