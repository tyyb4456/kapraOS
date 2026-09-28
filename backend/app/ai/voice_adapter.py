"""Voice-to-Master-Agent bridge (Step 11).

This module is the ONLY place where the LiveKit voice layer meets the
KapraOS brain — and it creates nothing new:

* The Master Deep Agent factory (``build_master_agent``) is reused as-is,
  with the SAME system prompt, tool registry, HITL config, subagents and
  skills as text chat.
* The SAME tenant-bound read tools (``build_read_tools``) and the SAME
  seven write tools (sale / customer payment / supplier payment / expense /
  purchase / customer return / supplier return) are attached. No
  voice-specific tools exist.
* Thread identity reuses ``_namespaced_thread`` (``shop_id:user_id:client``)
  so a voice thread can never cross tenants.
* HITL pauses are surfaced, never bypassed: a ``paused`` result must be
  resumed with explicit human decisions (tap Approve/Reject in the UI),
  exactly like text chat. Voice transcripts never auto-approve.
* Commits, idempotency receipts and Redis invalidation reuse
  ``_settle_transaction`` — the same boundary as ``/ai/chat``.

Transport note: the FastAPI backend and the LiveKit worker are separate
processes with separate process-local checkpointers. A voice thread lives
in the worker process that created it, so voice-thread approvals travel
over the LiveKit data channel back to that worker (see
``app.ai.voice_agent``), NOT through ``/ai/chat/resume``. Text-chat
semantics are unchanged.

Presentation note: ``to_speech_text`` only strips UI markdown/table syntax
so TTS speaks naturally (e.g. ``Rs.`` → ``rupees``). It never changes
numbers, never reorders facts, never drops mutation confirmations. The
Master Agent and business tools remain the sole authority for content.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import (
    READ_MASTER_TOOL_NAMES,
    WRITE_MASTER_TOOL_NAMES,
    build_master_agent,
)
from app.ai.state import tenant_context_from_user
from app.ai.tools.business_reads import build_read_tools
from app.ai.tools.expenses_write import build_expense_write_tools
from app.ai.tools.payments_write import build_payment_write_tools
from app.ai.tools.purchases_write import build_purchase_write_tools
from app.ai.tools.returns_write import build_return_write_tools
from app.ai.tools.sales_write import build_sale_write_tools
from app.ai.tools.supplier_payments_write import build_supplier_payment_write_tools
from app.api.ai import (
    HitlDecision,
    _extract_reply,
    _namespaced_thread,
    _serialize_interrupts,
    _settle_transaction,
    _validate_decisions,
    get_shared_checkpointer,
)
from app.models.user import User

# LiveKit data-channel topic for KapraOS voice control messages
# (approval notifications worker→frontend, decisions frontend→worker).
VOICE_DATA_TOPIC = "kapraos.voice"

# Voice threads are namespaced exactly like chat threads; the ``voice-``
# prefix only marks provenance so logs/debugging can tell interfaces apart.
VOICE_THREAD_PREFIX = "voice-"

# Maximum transcript length accepted per turn (mirrors ChatRequest).
VOICE_TRANSCRIPT_MAX_LENGTH = 2000


@dataclass
class VoiceTurnResult:
    """Outcome of one voice turn through the Master Agent."""

    status: str  # "done" | "paused"
    reply: str  # speech-ready text (already passed through to_speech_text)
    thread_id: str  # opaque client thread id (no tenant info leaked)
    pending: list[dict[str, Any]] = field(default_factory=list)


def new_voice_thread_id() -> str:
    """Generate a fresh opaque voice thread id (server-side only)."""
    return f"{VOICE_THREAD_PREFIX}{uuid.uuid4().hex}"


def build_voice_tools(db: AsyncSession, tenant: Any) -> tuple[list[Any], list[Any]]:
    """Build the SAME read + write tool sets text chat uses.

    Returns ``(read_tools, write_tools)``. Raises if the registry ever
    drifts from the Master Agent's canonical tool names — voice must never
    grow its own tools.
    """
    read_tools = build_read_tools(db, tenant)
    write_tools = [
        *build_sale_write_tools(db, tenant),
        *build_payment_write_tools(db, tenant),
        *build_supplier_payment_write_tools(db, tenant),
        *build_expense_write_tools(db, tenant),
        *build_purchase_write_tools(db, tenant),
        *build_return_write_tools(db, tenant),
    ]
    read_names = {getattr(t, "name", "") for t in read_tools}
    write_names = {getattr(t, "name", "") for t in write_tools}
    if read_names != set(READ_MASTER_TOOL_NAMES):
        raise RuntimeError(
            f"Voice read tools drifted from the Master Agent registry: {sorted(read_names)}"
        )
    if write_names != set(WRITE_MASTER_TOOL_NAMES):
        raise RuntimeError(
            f"Voice write tools drifted from the Master Agent registry: {sorted(write_names)}"
        )
    return read_tools, write_tools


async def run_voice_turn(
    *,
    db: AsyncSession,
    user: User,
    model: Any,
    transcript: str,
    thread_id: str | None = None,
) -> VoiceTurnResult:
    """Run one transcribed voice turn through the Master Agent.

    Args:
        db: Request-scoped async session (tools flush; this boundary commits
            via ``_settle_transaction`` exactly like ``/ai/chat``).
        user: Authenticated app user — the SOLE tenant source. The
            transcript, room name and any metadata are untrusted input.
        model: Chat model (``resolve_model("auto")`` in production, a fake
            model in tests).
        transcript: STT text for this turn (untrusted, validated like chat).
        thread_id: Opaque client thread id to continue, or None for a new
            voice thread.

    Returns a :class:`VoiceTurnResult` with speech-ready text.
    """
    text = (transcript or "").strip()
    if not text:
        raise ValueError("Empty voice transcript — nothing to send to the agent.")
    if len(text) > VOICE_TRANSCRIPT_MAX_LENGTH:
        raise ValueError(
            f"Voice transcript too long ({len(text)} chars, "
            f"max {VOICE_TRANSCRIPT_MAX_LENGTH})."
        )
    tenant = tenant_context_from_user(user)
    client_id = thread_id or new_voice_thread_id()
    _, namespaced = _namespaced_thread(user, client_id)
    read_tools, write_tools = build_voice_tools(db, tenant)
    agent = build_master_agent(
        model=model,
        checkpointer=get_shared_checkpointer(),
        extra_tools=read_tools,
        write_tools=write_tools,
    )
    first_message = f"[tenant shop_id={tenant.shop_id} user_id={tenant.user_id}] {text}"
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": first_message}]},
            config={"configurable": {"thread_id": namespaced}},
            version="v2",
        )
    except Exception as exc:
        raise VoiceAgentError(f"AI provider error: {exc}") from exc
    return await _voice_turn_result(db, result, client_id, tenant.shop_id)


async def resume_voice_turn(
    *,
    db: AsyncSession,
    user: User,
    model: Any,
    thread_id: str,
    decisions: list[dict[str, Any]],
) -> VoiceTurnResult:
    """Resume a paused voice turn with explicit human HITL decisions.

    ``decisions`` uses the same shape as ``/ai/chat/resume``
    (``approve`` / ``edit`` / ``reject``+message / ``respond``+message) and
    is validated by the SAME ``_validate_decisions``. There is no
    voice-specific approval path and no auto-approval: callers must forward
    decisions the shopkeeper tapped/confirmed explicitly.
    """
    if not (thread_id or "").strip():
        raise ValueError("A voice thread_id is required to resume.")
    validated = _validate_hitl_dicts(decisions)
    tenant = tenant_context_from_user(user)
    _, namespaced = _namespaced_thread(user, thread_id)
    read_tools, write_tools = build_voice_tools(db, tenant)
    agent = build_master_agent(
        model=model,
        checkpointer=get_shared_checkpointer(),
        extra_tools=read_tools,
        write_tools=write_tools,
    )
    try:
        from langgraph.types import Command

        result = await agent.ainvoke(
            Command(resume={"decisions": validated}),
            config={"configurable": {"thread_id": namespaced}},
            version="v2",
        )
    except Exception as exc:
        # Mirror /ai/chat/resume: no pending approval surfaces as a
        # conflict-shaped error instead of crashing the voice session.
        from langgraph.errors import InvalidUpdateError

        if isinstance(exc, InvalidUpdateError):
            raise VoiceNoPendingApprovalError(
                "No pending approval on this voice conversation."
            ) from exc
        raise VoiceAgentError(f"AI provider error: {exc}") from exc
    return await _voice_turn_result(db, result, thread_id, tenant.shop_id)


async def _voice_turn_result(
    db: AsyncSession,
    result: Any,
    client_thread_id: str,
    shop_id: Any,
) -> VoiceTurnResult:
    """Shape an agent invoke result as done/paused with speech-ready text."""
    interrupts = list(getattr(result, "interrupts", None) or [])
    if interrupts:
        pending = _serialize_interrupts(interrupts)
        # A paused run performed no approved mutation (the interrupt fires
        # BEFORE the tool executes) — _settle_transaction leaves it alone.
        await _settle_transaction(db, "paused", shop_id, result)
        return VoiceTurnResult(
            status="paused",
            reply=to_speech_text(approval_speech_prompt(pending)),
            thread_id=client_thread_id,
            pending=pending,
        )
    reply = _extract_reply(result)
    await _settle_transaction(db, "done", shop_id, result)
    return VoiceTurnResult(
        status="done",
        reply=to_speech_text(reply or "Theek hai, ho gaya."),
        thread_id=client_thread_id,
        pending=[],
    )


def _validate_hitl_dicts(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate raw decision dicts with the SAME rules as text chat."""
    try:
        parsed = [HitlDecision.model_validate(d) for d in decisions or []]
    except Exception as exc:
        raise ValueError(f"Malformed HITL decisions: {exc}") from exc
    return _validate_decisions(parsed)


def approval_speech_prompt(pending: list[dict[str, Any]]) -> str:
    """Short spoken notice for a HITL pause (no business facts invented)."""
    names = [str(p.get("name", "action")) for p in pending or []]
    pretty = ", ".join(names) if names else "action"
    return (
        f"Is {pretty} ke liye aap ki approval chahiye. "
        "Screen par approve ya reject karein."
    )


_MARKDOWN_CHARS_RE = re.compile(r"[*_`#>|~]")
_BULLET_LINE_RE = re.compile(r"(?m)^\s*[-*•\d]+\s*[.)]?\s+")
_WHITESPACE_RE = re.compile(r"\s+")
_RS_RE = re.compile(r"\bRs\.?", re.IGNORECASE)


def to_speech_text(reply: str) -> str:
    """Convert an agent reply to speech-friendly text (presentation only).

    Strips chat-UI markdown/table syntax and expands ``Rs.`` to ``rupees``
    so TTS speaks naturally. Digits, amounts, names and facts pass through
    byte-identical — this function MUST NOT change authoritative numbers.
    """
    text = reply or ""
    text = _RS_RE.sub("rupees", text)
    text = text.replace("|", ", ")
    text = _MARKDOWN_CHARS_RE.sub("", text)
    text = _BULLET_LINE_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    # Tidy artefacts of table stripping (", ,", leading commas).
    text = re.sub(r"(,\s*){2,}", ", ", text)
    text = re.sub(r"(^[\s,;.]+|[\s,;.]+$)", "", text)
    return text or reply


class VoiceAgentError(RuntimeError):
    """The Master Agent call failed (provider/DB); safe to speak generically."""


class VoiceNoPendingApprovalError(VoiceAgentError):
    """Resume attempted on a voice thread with nothing pending (HTTP 409 twin)."""


__all__ = [
    "VOICE_DATA_TOPIC",
    "VOICE_THREAD_PREFIX",
    "VOICE_TRANSCRIPT_MAX_LENGTH",
    "VoiceAgentError",
    "VoiceNoPendingApprovalError",
    "VoiceTurnResult",
    "approval_speech_prompt",
    "build_voice_tools",
    "new_voice_thread_id",
    "resume_voice_turn",
    "run_voice_turn",
    "to_speech_text",
]
