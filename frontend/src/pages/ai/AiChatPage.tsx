import { useEffect, useRef, useState } from 'react';
import { Bot, Send, Plus, ShieldAlert, Check, X } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  Textarea,
} from '../../components/ui/index.ts';
import {
  resumeAiChat,
  sendAiChat,
  type AiChatResponse,
  type AiPendingAction,
} from '../../lib/api/ai.ts';

const THREAD_STORAGE_KEY = 'kapraos-ai-chat-thread';

interface ChatMessage {
  id: number;
  role: 'user' | 'assistant';
  text: string;
}

let messageSeq = 0;
function nextId() {
  messageSeq += 1;
  return messageSeq;
}

function formatArgs(args: Record<string, unknown>): string {
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
}

export function AiChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [threadId, setThreadId] = useState<string | null>(() => {
    try {
      return localStorage.getItem(THREAD_STORAGE_KEY);
    } catch {
      return null;
    }
  });
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [pending, setPending] = useState<AiPendingAction[] | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [showRejectBox, setShowRejectBox] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, pending, sending]);

  const applyResponse = (data: AiChatResponse) => {
    try {
      localStorage.setItem(THREAD_STORAGE_KEY, data.thread_id);
    } catch {
      // private mode etc. - chat still works for this session
    }
    setThreadId(data.thread_id);
    if (data.status === 'paused') {
      setPending(data.interrupts ?? []);
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'assistant',
          text: 'This action needs your approval before I run it. Review it below.',
        },
      ]);
    } else {
      setPending(null);
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'assistant', text: data.reply || '(no reply)' },
      ]);
    }
  };

  const handleError = (err: unknown) => {
    if (err instanceof Error && 'status' in err && (err as { status: number }).status === 503) {
      setNotConfigured(true);
      return;
    }
    setError(err instanceof Error ? err.message : 'Chat request failed');
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending || pending) return;
    setError(null);
    setNotConfigured(false);
    setMessages((prev) => [...prev, { id: nextId(), role: 'user', text }]);
    setInput('');
    try {
      setSending(true);
      const data = await sendAiChat(text, threadId);
      applyResponse(data);
    } catch (err) {
      handleError(err);
    } finally {
      setSending(false);
    }
  };

  const handleApprove = async () => {
    if (!threadId || !pending || sending) return;
    setError(null);
    try {
      setSending(true);
      const data = await resumeAiChat(
        threadId,
        pending.map(() => ({ type: 'approve' as const })),
      );
      setShowRejectBox(false);
      setRejectReason('');
      applyResponse(data);
    } catch (err) {
      handleError(err);
    } finally {
      setSending(false);
    }
  };

  const handleReject = async () => {
    if (!threadId || !pending || sending) return;
    if (!rejectReason.trim()) {
      setError('Tell the assistant what to do instead — a rejection needs a message.');
      return;
    }
    setError(null);
    try {
      setSending(true);
      const data = await resumeAiChat(
        threadId,
        pending.map(() => ({ type: 'reject' as const, message: rejectReason.trim() })),
      );
      setShowRejectBox(false);
      setRejectReason('');
      applyResponse(data);
    } catch (err) {
      handleError(err);
    } finally {
      setSending(false);
    }
  };

  const handleNewChat = () => {
    try {
      localStorage.removeItem(THREAD_STORAGE_KEY);
    } catch {
      // ignore
    }
    setThreadId(null);
    setMessages([]);
    setPending(null);
    setShowRejectBox(false);
    setRejectReason('');
    setError(null);
    setNotConfigured(false);
  };

  return (
    <PageContainer maxWidth="narrow">
      <PageHeader
        title="AI Assistant"
        description="Ask about your shop in plain words. Side-effecting actions pause here for your approval."
        breadcrumbs={[{ label: 'AI Assistant' }]}
        actions={
          <Button variant="outline" size="sm" leftIcon={<Plus className="w-4 h-4" />} onClick={handleNewChat}>
            New chat
          </Button>
        }
      />

      {notConfigured && (
        <Alert variant="warning" title="AI model not configured">
          The backend has no <span className="font-mono">XKIRO_API_KEY</span> yet. Add it to{' '}
          <span className="font-mono">backend/.env</span> and restart the API server — then come
          back and chat.
        </Alert>
      )}

      {error && (
        <Alert variant="danger" title="Something went wrong">
          {error}
        </Alert>
      )}

      <Card>
        <CardContent className="space-y-4 pt-5">
          {messages.length === 0 && !pending && (
            <div className="flex flex-col items-center gap-2 py-10 text-center">
              <div className="w-11 h-11 rounded-full bg-zinc-900 dark:bg-zinc-100 flex items-center justify-center text-white dark:text-zinc-900">
                <Bot className="w-5 h-5" />
              </div>
              <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                Assalam-o-Alaikum! What can I do for your shop today?
              </p>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-sm">
                Try “What is khata?”, “Kitna stock hai?” — as new capabilities land, this same
                chat gets more powerful. Nothing here can switch shops or bypass approvals.
              </p>
            </div>
          )}

          <div className="space-y-3">
            {messages.map((msg) => (
              <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div
                  className={`max-w-[85%] rounded-lg px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
                    msg.role === 'user'
                      ? 'bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900'
                      : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100'
                  }`}
                >
                  {msg.text}
                </div>
              </div>
            ))}
            {sending && (
              <div className="flex justify-start">
                <div className="rounded-lg px-3.5 py-2.5 bg-zinc-100 dark:bg-zinc-800">
                  <span className="inline-flex gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce" />
                    <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce [animation-delay:150ms]" />
                    <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce [animation-delay:300ms]" />
                  </span>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {pending && pending.length > 0 && (
            <div className="rounded-lg border border-amber-200 dark:border-amber-500/30 bg-amber-50 dark:bg-amber-500/10 p-3.5 space-y-3">
              <div className="flex items-center gap-2 text-xs font-semibold text-amber-800 dark:text-amber-200">
                <ShieldAlert className="w-4 h-4" />
                Approval needed — {pending.length} action{pending.length > 1 ? 's' : ''}
              </div>
              {pending.map((action, idx) => (
                <div
                  key={idx}
                  className="rounded-md border border-amber-200 dark:border-amber-500/30 bg-white dark:bg-zinc-950 p-2.5"
                >
                  <div className="flex items-center gap-2 text-xs">
                    <Badge variant="warning" size="sm">{action.name}</Badge>
                  </div>
                  <pre className="mt-1.5 overflow-x-auto font-mono text-[11px] leading-relaxed text-zinc-700 dark:text-zinc-300">
                    {formatArgs(action.args)}
                  </pre>
                </div>
              ))}
              {!showRejectBox ? (
                <div className="flex flex-wrap gap-2">
                  <Button variant="primary" size="sm" leftIcon={<Check className="w-4 h-4" />} onClick={handleApprove} isLoading={sending}>
                    Approve{pending.length > 1 ? ' all' : ''}
                  </Button>
                  <Button variant="outline" size="sm" leftIcon={<X className="w-4 h-4" />} onClick={() => setShowRejectBox(true)} disabled={sending}>
                    Reject
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  <Textarea
                    label="What should the assistant do instead?"
                    value={rejectReason}
                    onChange={(e) => setRejectReason(e.target.value)}
                    rows={2}
                    placeholder="e.g. Don't run this. Just tell me today's total sales instead."
                  />
                  <div className="flex flex-wrap gap-2">
                    <Button variant="danger" size="sm" onClick={handleReject} isLoading={sending}>
                      Confirm reject
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setShowRejectBox(false)} disabled={sending}>
                      Back
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}

          <div className="flex items-end gap-2">
            <div className="flex-1">
              <Textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                rows={2}
                placeholder={pending ? 'Approve or reject the pending action first…' : 'Ask anything… (Enter to send)'}
                disabled={sending || !!pending}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
              />
            </div>
            <Button
              variant="primary"
              size="md"
              leftIcon={<Send className="w-4 h-4" />}
              onClick={handleSend}
              isLoading={sending}
              disabled={!input.trim() || !!pending}
            >
              Send
            </Button>
          </div>
        </CardContent>
      </Card>
    </PageContainer>
  );
}
