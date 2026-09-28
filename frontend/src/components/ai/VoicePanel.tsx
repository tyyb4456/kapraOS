import { useEffect, useRef, useState } from 'react';
import { Check, Mic, MicOff, PhoneOff, ShieldAlert, X } from 'lucide-react';
import { Alert, Badge, Button, Card, CardContent } from '../ui/index.ts';
import { resumeAiChat, type AiHitlDecision, type AiPendingAction } from '../../lib/api/ai.ts';
import { getVoiceConfig, requestVoiceToken } from '../../lib/api/voice.ts';
import { VoiceConnection, type VoiceControlMessage } from '../../lib/voice/livekitVoice.ts';

type VoiceState = 'idle' | 'connecting' | 'connected' | 'error';

interface VoicePanelProps {
  onAgentReply: (text: string) => void;
}

function allowsDecision(action: AiPendingAction, decision: string): boolean {
  if (Array.isArray(action.allowed_decisions) && action.allowed_decisions.length > 0) {
    return action.allowed_decisions.includes(decision);
  }
  return decision === 'approve' || decision === 'reject';
}

export function VoicePanel({ onAgentReply }: VoicePanelProps) {
  const [voiceAvailable, setVoiceAvailable] = useState<boolean | null>(null);
  const [state, setState] = useState<VoiceState>('idle');
  const [muted, setMuted] = useState(false);
  const [agentJoined, setAgentJoined] = useState(false);
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const [voiceThreadId, setVoiceThreadId] = useState<string | null>(null);
  const [pending, setPending] = useState<AiPendingAction[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const connectionRef = useRef<VoiceConnection | null>(null);

  useEffect(() => {
    let cancelled = false;
    getVoiceConfig()
      .then((cfg) => {
        if (!cancelled) setVoiceAvailable(cfg.enabled);
      })
      .catch(() => {
        if (!cancelled) setVoiceAvailable(false);
      });
    return () => {
      cancelled = true;
      void connectionRef.current?.disconnect();
      connectionRef.current = null;
    };
  }, []);

  const handleControlMessage = (message: VoiceControlMessage) => {
    if (message.type === 'approval_pending') {
      setVoiceThreadId(message.thread_id);
      setPending(message.actions ?? []);
    } else if (message.type === 'reply_done') {
      setPending(null);
      if (message.reply) onAgentReply(message.reply);
    }
  };

  const handleConnect = async () => {
    setError(null);
    setState('connecting');
    try {
      const token = await requestVoiceToken();
      const connection = new VoiceConnection();
      connectionRef.current = connection;
      setVoiceThreadId(token.thread_id);
      await connection.connect(token.server_url, token.participant_token, {
        onAgentJoined: () => setAgentJoined(true),
        onAgentLeft: () => {
          setAgentJoined(false);
          setAgentSpeaking(false);
        },
        onAgentSpeaking: (speaking) => setAgentSpeaking(speaking),
        onControlMessage: handleControlMessage,
        onDisconnected: () => {
          setState('idle');
          setAgentJoined(false);
          setAgentSpeaking(false);
          setPending(null);
        },
      });
      setState('connected');
    } catch (err) {
      setState('error');
      setError(err instanceof Error ? err.message : 'Could not start voice.');
    }
  };

  const handleDisconnect = async () => {
    await connectionRef.current?.disconnect();
    connectionRef.current = null;
    setState('idle');
    setMuted(false);
    setAgentJoined(false);
    setAgentSpeaking(false);
    setPending(null);
    setError(null);
  };

  const handleMuteToggle = async () => {
    const connection = connectionRef.current;
    if (!connection) return;
    try {
      await connection.setMicrophoneEnabled(muted);
      setMuted(!muted);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Microphone toggle failed.');
    }
  };

  const handleApprove = async () => {
    if (!voiceThreadId || !pending || busy) return;
    setBusy(true);
    setError(null);
    try {
      const decisions: AiHitlDecision[] = pending.map(() => ({ type: 'approve' as const }));
      // Approvals for a voice thread are delivered over the voice data
      // channel to the worker that owns the thread (text /ai/chat/resume
      // cannot see worker-local threads — see Step 11 notes).
      await connectionRef.current?.sendDecisions(voiceThreadId, decisions);
    } catch (err) {
      // Fall back to the shared text-chat resume path (works when the
      // thread lives in the backend process, e.g. loopback dev setups).
      try {
        const decisions: AiHitlDecision[] = pending.map(() => ({ type: 'approve' as const }));
        const data = await resumeAiChat(voiceThreadId, decisions);
        setPending(null);
        if (data.reply) onAgentReply(data.reply);
      } catch {
        setError(err instanceof Error ? err.message : 'Approval failed.');
      }
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async () => {
    if (!voiceThreadId || !pending || busy) return;
    setBusy(true);
    setError(null);
    try {
      const decisions: AiHitlDecision[] = pending.map(() => ({
        type: 'reject' as const,
        message: 'Do not run this. Just answer my question instead.',
      }));
      await connectionRef.current?.sendDecisions(voiceThreadId, decisions);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Rejection failed.');
    } finally {
      setBusy(false);
    }
  };

  if (voiceAvailable === false) return null;

  const statusLine =
    state === 'connected'
      ? agentSpeaking
        ? 'Assistant is speaking…'
        : agentJoined
          ? muted
            ? 'Connected — microphone muted'
            : 'Connected — listening…'
          : 'Connected — waiting for the assistant…'
      : null;

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-3 pt-5">
        {state === 'connecting' ? (
          <Button variant="outline" size="md" disabled isLoading>
            Connecting…
          </Button>
        ) : state === 'idle' || state === 'error' ? (
          <Button
            variant="outline"
            size="md"
            leftIcon={<Mic className="w-4 h-4" />}
            onClick={handleConnect}
            disabled={voiceAvailable === null}
          >
            {voiceAvailable === null ? 'Checking voice…' : 'Talk with voice'}
          </Button>
        ) : (
          <>
            <Badge variant={agentSpeaking ? 'info' : 'success'} size="sm">
              <span className="inline-flex items-center gap-1.5">
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    agentSpeaking ? 'bg-sky-500 animate-pulse' : 'bg-emerald-500'
                  }`}
                />
                Voice {agentSpeaking ? 'speaking' : 'live'}
              </span>
            </Badge>
            <span className="text-xs text-zinc-500 dark:text-zinc-400">{statusLine}</span>
            <span className="flex-1" />
            <Button
              variant="outline"
              size="sm"
              leftIcon={muted ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
              onClick={handleMuteToggle}
            >
              {muted ? 'Unmute' : 'Mute'}
            </Button>
            <Button
              variant="danger"
              size="sm"
              leftIcon={<PhoneOff className="w-4 h-4" />}
              onClick={handleDisconnect}
            >
              End voice
            </Button>
          </>
        )}

        {error && (
          <div className="w-full">
            <Alert variant="danger" title="Voice error">
              {error}
            </Alert>
          </div>
        )}

        {pending && pending.length > 0 && (
          <div className="w-full rounded-lg border border-amber-200 dark:border-amber-500/30 bg-amber-50 dark:bg-amber-500/10 p-3 space-y-2">
            <div className="flex items-center gap-2 text-xs font-semibold text-amber-800 dark:text-amber-200">
              <ShieldAlert className="w-4 h-4" />
              Voice action needs approval — {pending.map((a) => a.name).join(', ')}
            </div>
            <div className="flex flex-wrap gap-2">
              {pending.every((a) => allowsDecision(a, 'approve')) && (
                <Button
                  variant="primary"
                  size="sm"
                  leftIcon={<Check className="w-4 h-4" />}
                  onClick={handleApprove}
                  isLoading={busy}
                >
                  Approve
                </Button>
              )}
              {pending.every((a) => allowsDecision(a, 'reject')) && (
                <Button
                  variant="outline"
                  size="sm"
                  leftIcon={<X className="w-4 h-4" />}
                  onClick={handleReject}
                  disabled={busy}
                >
                  Reject
                </Button>
              )}
            </div>
            <p className="text-[11px] text-amber-700 dark:text-amber-300">
              Voice never auto-approves. Approving runs the same checked tool as text chat.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
