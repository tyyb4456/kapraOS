import { beforeEach, describe, expect, it } from 'vitest';
import {
  clearPendingInviteToken,
  getPendingInviteToken,
  rememberInviteToken,
} from '../inviteToken.ts';

describe('pending invite token', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('round-trips a token through sessionStorage', () => {
    rememberInviteToken('invite-token-123');
    expect(getPendingInviteToken()).toBe('invite-token-123');
  });

  it('clears the stored token', () => {
    rememberInviteToken('invite-token-123');
    clearPendingInviteToken();
    expect(getPendingInviteToken()).toBeNull();
  });

  it('returns null when no token was stored', () => {
    expect(getPendingInviteToken()).toBeNull();
  });
});
