"""KapraOS realtime voice worker (Step 11).

Run separately from the FastAPI backend (LiveKit Cloud handles the
realtime infrastructure)::

    cd backend
    uv run python -m app.ai.voice_agent dev      # development
    uv run python -m app.ai.voice_agent start    # production
    # or: lk agent dev   (LiveKit CLI, same directory)

Architecture (voice is transport, NOT a second brain)::

    mic -> LiveKit -> STT -> llm_node -> Master Deep Agent
        -> existing tools/services -> reply text -> TTS -> speaker

* STT/TTS come from LiveKit Inference (see ``app.ai.voice_config``).
* The session LLM is a constructor placeholder only: ``KapraVoiceAgent``
  overrides ``llm_node`` and delegates EVERY turn to the existing Master
  Deep Agent via ``app.ai.voice_adapter`` (same factory, same tools, same
  HITL, same Redis-backed services). No voice tools, no voice prompt
  engineering of business logic, no direct DB writes.
* Tenant identity is re-established server-side on every turn: the worker
  reads the linked participant identity (minted by ``POST /ai/voice/token``
  from the Clerk-authenticated user), loads the ``User`` row from the
  database, and derives ``TenantContext`` from it. Room names, attributes,
  transcripts and metadata are untrusted transport hints.
* HITL is preserved: a paused turn is spoken as an approval request AND
  published on the data channel; the shopkeeper's explicit Approve/Reject
  decision comes back over the data channel and resumes the worker-owned
  thread. Voice transcripts never auto-approve. Text-chat semantics are
  unchanged (voice threads live in this worker process — see
  ``app.ai.voice_adapter`` for why).
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterable
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger("kapraos.voice")

load_dotenv(".env")

from app.ai.voice_adapter import (
    VOICE_DATA_TOPIC,
    VoiceAgentError,
    new_voice_thread_id,
    resume_voice_turn,
    run_voice_turn,
)
from app.ai.voice_config import get_voice_settings, is_voice_configured

try:
    from livekit import agents
    from livekit.agents import (
        Agent,
        AgentServer,
        AgentSession,
        JobContext,
        inference,
    )

    _LIVEKIT_AVAILABLE = True
except ImportError:  # pragma: no cover — unit tests cover the adapter instead
    agents = None  # type: ignore[assignment]
    Agent = object  # type: ignore[assignment, misc]
    AgentServer = None  # type: ignore[assignment]
    AgentSession = None  # type: ignore[assignment]
    JobContext = Any  # type: ignore[assignment, misc]
    inference = None  # type: ignore[assignment]
    _LIVEKIT_AVAILABLE = False


# Fixed scripted greeting (``say()``, not an LLM call): the Master Agent is
# the only brain, and no second model speaks on its behalf — not even hello.
VOICE_GREETING = (
    "Assalam-o-Alaikum! Main aap ka KapraOS assistant hoon. "
    "Aap stock, khata, ya payment ke baare mein pooch sakte hain."
)

# Spoken when tenant resolution or the provider call fails. Generic on
# purpose: never leak shop ids, tokens, or tracebacks over audio.
VOICE_ERROR_FALLBACK = "Maazrat, abhi jawab nahi de saka. Dobara koshish karein."

# Spoken when an approval decision arrives but nothing is pending.
VOICE_NOTHING_PENDING = (
    "Is waqt koi approval pending nahi hai. Aap apna sawal dobara pooch sakte hain."
)


def parse_voice_identity(identity: str) -> uuid.UUID:
    """Extract the user id minted by ``POST /ai/voice/token``.

    The identity has the form ``user-<uuid>``. Anything else is rejected —
    the worker never guesses a tenant.
    """
    text = (identity or "").strip()
    if not text.startswith("user-"):
        raise VoiceAgentError(f"Refusing untrusted voice identity {identity!r}.")
    try:
        return uuid.UUID(text[len("user-") :])
    except ValueError as exc:
        raise VoiceAgentError(
            f"Refusing malformed voice identity {identity!r}."
        ) from exc


async def resolve_voice_user(user_id: uuid.UUID) -> Any:
    """Load the authenticated app user for a voice session (server-side).

    This is the worker's authorization boundary: the participant identity is
    only a transport hint, and the ``User`` row (with its ``shop_id``) is
    authoritative — mirroring ``get_current_user`` + ``tenant_context``.
    """
    from sqlalchemy import select

    from app.database.session import AsyncSessionLocal
    from app.models.user import User

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise VoiceAgentError("Voice user is not provisioned.")
        if user.shop_id is None:
            raise VoiceAgentError("Voice user has no shop association.")
        session.expunge(user)
        return user


def resolve_voice_model() -> Any:
    """Chat model for the Master Agent inside voice turns (xKiro-backed)."""
    from app.ai.agent import resolve_model

    return resolve_model("auto")


def _latest_user_text(chat_ctx: Any) -> str:
    """Newest user message text from the LiveKit chat context."""
    try:
        accessor = getattr(chat_ctx, "messages", None)
        if callable(accessor):
            # LiveKit SDK: ChatContext.messages() is a method, not a property.
            messages = list(accessor())
        elif isinstance(accessor, (list, tuple)):
            messages = list(accessor)
        else:
            # Fallback to raw items (covers FunctionCall/chat-item mixes).
            items = getattr(chat_ctx, "items", None)
            if callable(items):
                items = items()
            messages = list(items or [])
    except Exception:  # noqa: BLE001 — untrusted SDK shape, fail to empty text
        logger.debug("Could not read voice chat context.", exc_info=True)
        return ""
    for message in reversed(messages):
        try:
            if getattr(message, "role", None) != "user":
                continue
            text: Any = ""
            if hasattr(message, "text_content"):
                attr = getattr(message, "text_content")
                # text_content is a property on ChatMessage; but be defensive
                # if a mock replaces it with a method.
                text = attr() if callable(attr) else attr
            if not isinstance(text, str) or not text.strip():
                # Fallback: scan raw content list for string parts
                # (e.g. content=[transcript] as built by AgentActivity).
                content = getattr(message, "content", None)
                if isinstance(content, (list, tuple)):
                    parts = [c for c in content if isinstance(c, str) and c.strip()]
                    if parts:
                        text = "\n".join(parts)
                    else:
                        continue
                else:
                    continue
            if isinstance(text, str) and text.strip():
                return text.strip()
        except Exception:  # noqa: BLE001 — skip one malformed message, keep scanning
            logger.debug("Skipping malformed chat message in voice turn.")
            continue
    logger.debug(
        "No user text found in voice chat context (%d items scanned).",
        len(messages),
    )
    return ""


def _linked_identity(agent: Any) -> tuple[str, dict[str, str]]:
    """Linked participant identity + attributes (transport hints, untrusted)."""
    try:
        session = getattr(agent, "session", None)
        room_io_state = getattr(session, "room_io", None)
        participant = getattr(room_io_state, "linked_participant", None)
        identity = str(getattr(participant, "identity", "") or "")
        try:
            attributes = dict(getattr(participant, "attributes", None) or {})
        except Exception:  # noqa: BLE001 — attributes are optional transport hints
            attributes = {}
        return identity, {str(k): str(v) for k, v in attributes.items()}
    except Exception:  # noqa: BLE001 — unlinked session, fail to empty identity
        return "", {}


class KapraVoiceAgent(Agent):  # type: ignore[misc]
    """LiveKit agent shell around the KapraOS Master Deep Agent.

    Presentation layer only: STT text in, Master Agent reply out. Holds no
    business logic and no tools of its own (``tools=[]`` — the Master Agent
    brings the canonical registry per turn). Tenant state is resolved fresh
    from the linked participant on every turn, never cached across rooms:
    one room = one tenant, enforced by the token endpoint + DB lookup.
    """

    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are the realtime voice interface of the KapraOS shop "
                "assistant. Every user turn is answered by the KapraOS Master "
                "Agent; you only speak its exact reply. Never invent amounts, "
                "names, or confirmations."
            ),
            tools=[],
        )
        self._voice_model: Any = None
        self._voice_threads: dict[str, str] = {}

    async def _turn_context(self) -> tuple[Any, str]:
        """Resolve (user, thread_id) for the current room, server-side."""
        identity, attributes = _linked_identity(self)
        user_id = parse_voice_identity(identity)
        user = await resolve_voice_user(user_id)
        thread_id = attributes.get("kapraos_thread_id") or self._voice_threads.get(
            identity
        )
        if not thread_id:
            thread_id = new_voice_thread_id()
        self._voice_threads[identity] = thread_id
        return user, thread_id

    async def llm_node(
        self,
        chat_ctx: Any,
        tools: list[Any],
        model_settings: Any,
    ) -> AsyncIterable[Any]:
        """Delegate the turn to the Master Agent; yield its reply as text."""
        transcript = _latest_user_text(chat_ctx)
        if not transcript:
            yield "Maazrat, main sun nahi saka. Dobara farmaein."
            return
        try:
            user, thread_id = await self._turn_context()
            if self._voice_model is None:
                self._voice_model = resolve_voice_model()
            from app.database.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                turn = await run_voice_turn(
                    db=db,
                    user=user,
                    model=self._voice_model,
                    transcript=transcript,
                    thread_id=thread_id,
                )
            if turn.status == "paused":
                await _publish_voice_event(
                    self,
                    {
                        "type": "approval_pending",
                        "thread_id": turn.thread_id,
                        "actions": turn.pending,
                    },
                )
            yield turn.reply or VOICE_ERROR_FALLBACK
        except VoiceAgentError as exc:
            logger.warning("Voice turn failed safely: %s", exc)
            yield VOICE_ERROR_FALLBACK
        except Exception:
            logger.exception("Unexpected voice turn failure")
            yield VOICE_ERROR_FALLBACK  # never drop the voice session


async def _publish_voice_event(agent: Any, payload: dict[str, Any]) -> None:
    """Publish a control message to the room (best effort, never raises).

    ``publish_data`` takes keyword-only args (``reliable``, ``topic``) and
    returns None in the installed SDK — handled defensively so both sync
    and awaitable shapes work.
    """
    try:
        session = getattr(agent, "session", None)
        room = getattr(session, "room", None) if session is not None else None
        local = getattr(room, "local_participant", None) if room is not None else None
        if local is None:
            return
        publish = getattr(local, "publish_data", None)
        if publish is None:
            return
        result = publish(
            json.dumps(payload, default=str),
            reliable=True,
            topic=VOICE_DATA_TOPIC,
        )
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:  # noqa: BLE001 — observability only
        logger.warning("Could not publish voice event: %s", exc)


def _parse_data_message(raw: Any) -> dict[str, Any] | None:
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8")
        if not isinstance(raw, str):
            return None
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001 — malformed data-channel bytes are ignored
        return None


def _coerce_data_args(args: Any, kwargs: Any) -> tuple[Any, str, str]:
    """Normalise DataPacket handler args to (raw, identity, topic)."""
    packet = args[0] if args else kwargs.get("packet", kwargs.get("data"))
    raw = getattr(packet, "data", packet)
    participant = getattr(packet, "participant", None)
    identity = str(getattr(participant, "identity", "") or "")
    topic = str(getattr(packet, "topic", "") or "")
    return raw, identity, topic


async def _handle_voice_decisions(
    agent: KapraVoiceAgent, payload: dict[str, Any]
) -> None:
    """Resume a paused voice thread with the shopkeeper's explicit decision."""
    decisions = payload.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        return
    try:
        user, thread_id = await agent._turn_context()
    except VoiceAgentError as exc:
        logger.warning("Refusing voice decisions without a tenant: %s", exc)
        return
    if str(payload.get("thread_id", thread_id)) != thread_id:
        logger.warning("Ignoring voice decisions for a foreign thread.")
        return
    try:
        if agent._voice_model is None:
            agent._voice_model = resolve_voice_model()
        from app.database.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            turn = await resume_voice_turn(
                db=db,
                user=user,
                model=agent._voice_model,
                thread_id=thread_id,
                decisions=decisions,
            )
        await _publish_voice_event(
            agent,
            {
                "type": "reply_done" if turn.status == "done" else "approval_pending",
                "thread_id": turn.thread_id,
                "reply": turn.reply,
                "actions": turn.pending,
            },
        )
        session = getattr(agent, "session", None)
        say = getattr(session, "say", None) if session is not None else None
        if callable(say):
            result = say(turn.reply or VOICE_ERROR_FALLBACK)
            if asyncio.iscoroutine(result):
                await result
    except Exception as exc:  # noqa: BLE001 — speak, don't crash
        logger.warning("Voice resume failed safely: %s", exc)
        try:
            session = getattr(agent, "session", None)
            say = getattr(session, "say", None) if session is not None else None
            if callable(say):
                result = say(VOICE_NOTHING_PENDING)
                if asyncio.iscoroutine(result):
                    await result
        except Exception:  # noqa: BLE001 — last-resort speech already attempted
            logger.debug("Could not speak the voice fallback message.")


def build_voice_session() -> Any:
    """Build the STT → (Master Agent) → TTS session (LiveKit Inference)."""
    settings = get_voice_settings()
    stt: Any = inference.STT(
        model=settings.voice_stt_model, language=settings.voice_stt_language
    )
    # Constructor placeholder only — KapraVoiceAgent.llm_node delegates every
    # turn to the KapraOS Master Deep Agent. Kept on Inference so no extra
    # provider key is needed.
    llm_placeholder: Any = inference.LLM(model=settings.voice_llm_model)
    if settings.voice_tts_voice:
        tts: Any = inference.TTS(
            model=settings.voice_tts_model, voice=settings.voice_tts_voice
        )
    else:
        tts = inference.TTS(model=settings.voice_tts_model)
    from livekit.agents import TurnHandlingOptions

    return AgentSession(
        stt=stt,
        llm=llm_placeholder,
        tts=tts,
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
        ),
    )


server: Any = None
if _LIVEKIT_AVAILABLE:
    server = AgentServer()
    _voice_agent_name = get_voice_settings().voice_agent_name

    @server.rtc_session(agent_name=_voice_agent_name)
    async def kapraos_voice(ctx: JobContext) -> None:
        """LiveKit job entrypoint: one room, one tenant, one Master Agent."""
        settings = get_voice_settings()
        if not is_voice_configured(settings=settings):
            logger.error(
                "LIVEKIT_URL/API_KEY/API_SECRET missing — refusing voice job. "
                "See backend/.env.example."
            )
            return

        agent = KapraVoiceAgent()
        session = build_voice_session()

        # Explicit Approve/Reject decisions arrive over the data channel from
        # the shopkeeper's own UI (same thread, same tenant). The sender must
        # be the linked participant; transcripts never approve.
        room = getattr(ctx, "room", None)
        try:
            on = getattr(room, "on", None)
            if callable(on):

                def _sink(*args: Any, **kwargs: Any) -> Any:
                    raw, identity, topic = _coerce_data_args(args, kwargs)

                    async def _route() -> None:
                        if topic != VOICE_DATA_TOPIC:
                            return
                        payload = _parse_data_message(raw)
                        if payload is None or payload.get("type") != "decisions":
                            return
                        linked, _ = _linked_identity(agent)
                        if not linked or identity != linked:
                            logger.warning(
                                "Ignoring voice decisions from an unknown sender."
                            )
                            return
                        await _handle_voice_decisions(agent, payload)

                    return asyncio.create_task(_route())

                on("data_received")(_sink)
        except Exception as exc:  # noqa: BLE001 — voice Q&A works regardless
            logger.warning("Could not attach voice data handler: %s", exc)

        await session.start(room=ctx.room, agent=agent)
        # Scripted greeting via say(): no LLM speaks on the Master's behalf.
        await session.say(VOICE_GREETING)


if __name__ == "__main__":  # pragma: no cover — live process entrypoint
    if not _LIVEKIT_AVAILABLE:
        raise SystemExit(
            "livekit-agents is not installed. Run `uv sync` in backend/ first."
        )
    agents.cli.run_app(server)
