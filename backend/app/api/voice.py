"""LiveKit voice token endpoint (Step 11).

``POST /ai/voice/token`` mints a short-lived LiveKit room token for the
authenticated user so the browser can join a realtime voice room; the
KapraOS voice worker (``app.ai.voice_agent``) is dispatched into the same
room and bridges STT/TTS around the existing Master Deep Agent.

Tenant isolation (critical):

* The room name, participant identity and thread id are ALL generated
  server-side from the verified Clerk user (``CurrentUserDep``). The
  request body carries NO tenant fields — there is no ``shop_id`` to forge,
  no room to pick, no identity to spoof.
* Room/identity/metadata are transport hints only. The worker re-establishes
  authorization server-side by loading the ``User`` row from the database
  and deriving ``TenantContext`` from it — exactly like ``/ai/chat``.
* Tokens grant ``roomJoin`` for ONE generated room only, publish+subscribe
  for microphone/speaker media. They confer no KapraOS privileges by
  themselves; every brain call re-authenticates via the worker's DB lookup.

Response follows the LiveKit standardized token-endpoint shape
(``server_url`` + ``participant_token``) plus KapraOS extras (``room_name``,
``thread_id``, ``agent_name``) the frontend voice panel needs.
"""

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.ai.voice_adapter import VOICE_THREAD_PREFIX, new_voice_thread_id
from app.ai.voice_config import (
    VoiceNotConfiguredError,
    get_voice_settings,
)
from app.api.dependencies import CurrentUserDep

router = APIRouter(prefix="/ai/voice", tags=["ai-voice"])


class VoiceTokenRequest(BaseModel):
    """Token request body — deliberately tenant-free.

    Only an optional ``thread_id`` hint may be supplied (to continue the
    caller's own voice thread); it is still namespaced under the
    authenticated shop+user server-side, so one tenant can never address
    another's thread. Any other client-supplied field is ignored.
    """

    thread_id: str | None = Field(default=None, max_length=64)


class VoiceTokenResponse(BaseModel):
    server_url: str
    participant_token: str
    room_name: str
    thread_id: str
    agent_name: str


class VoiceConfigResponse(BaseModel):
    enabled: bool
    agent_name: str


def _short(value: Any, length: int = 8) -> str:
    return str(value).replace("-", "")[:length]


def _dispatch_config(agent_name: str) -> Any:
    """RoomConfiguration dispatching our voice worker (verified shape).

    Uses the installed ``livekit-api`` proto types (``RoomConfiguration``
    with ``agents=[RoomAgentDispatch(agent_name=...)]``) instead of a raw
    dict, so a shape drift fails loudly here at mint time rather than
    silently producing a token without dispatch.
    """
    from livekit.protocol import agent_dispatch, room

    return room.RoomConfiguration(
        agents=[agent_dispatch.RoomAgentDispatch(agent_name=agent_name)]
    )


@router.get("/config", response_model=VoiceConfigResponse)
async def voice_config(current_user: CurrentUserDep) -> VoiceConfigResponse:
    """Report whether realtime voice is available for this backend."""
    _ = current_user  # auth required; no per-user variation in V1.
    settings = get_voice_settings()
    enabled = bool(
        settings.livekit_url
        and settings.livekit_api_key
        and settings.livekit_api_secret
    )
    return VoiceConfigResponse(enabled=enabled, agent_name=settings.voice_agent_name)


@router.post(
    "/token", response_model=VoiceTokenResponse, status_code=status.HTTP_201_CREATED
)
async def voice_token(
    body: VoiceTokenRequest, current_user: CurrentUserDep
) -> VoiceTokenResponse:
    """Mint a LiveKit room token bound to the authenticated tenant."""
    settings = get_voice_settings()
    if not (
        settings.livekit_url
        and settings.livekit_api_key
        and settings.livekit_api_secret
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LiveKit voice is not configured. Set LIVEKIT_URL, "
                "LIVEKIT_API_KEY and LIVEKIT_API_SECRET in backend/.env "
                "from your LiveKit Cloud project, then restart the API server."
            ),
        )
    try:
        from livekit import api as livekit_api
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Voice dependencies are not installed. Install the backend "
                "with LiveKit support (livekit-agents, livekit-api)."
            ),
        ) from exc

    # All session identifiers are minted here from the authenticated user.
    # Nothing from the request body influences tenant selection.
    shop_part = _short(current_user.shop_id)
    user_part = _short(current_user.id)
    room_name = f"voice-{shop_part}-{user_part}-{uuid.uuid4().hex[:8]}"
    participant_identity = f"user-{current_user.id}"
    participant_name = (current_user.name or "Shopkeeper")[:64]
    thread_id = body.thread_id or new_voice_thread_id()
    if not thread_id.startswith(VOICE_THREAD_PREFIX):
        thread_id = f"{VOICE_THREAD_PREFIX}{thread_id}"

    try:
        token = livekit_api.AccessToken(
            settings.livekit_api_key, settings.livekit_api_secret
        )
        token = token.with_ttl(timedelta(minutes=10))
        token = token.with_identity(participant_identity)
        token = token.with_name(participant_name)
        token = token.with_grants(
            livekit_api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )
        # Transport hints only — the worker re-resolves the User row from
        # the database and derives TenantContext from it. Never trusted for
        # authorization on their own.
        token = token.with_attributes(
            {
                "kapraos_thread_id": thread_id,
                "kapraos_user_id": str(current_user.id),
                "kapraos_shop_id": str(current_user.shop_id),
            }
        )
        # Ask LiveKit to dispatch our voice worker into this room. Even
        # without this the worker (``lk agent dev`` / ``start``) joins new
        # rooms by default, so a dispatch failure never blocks voice.
        token = token.with_room_config(_dispatch_config(settings.voice_agent_name))
        participant_token = token.to_jwt()
    except VoiceNotConfiguredError:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not mint LiveKit voice token: {exc}",
        ) from exc

    return VoiceTokenResponse(
        server_url=settings.livekit_url,
        participant_token=participant_token,
        room_name=room_name,
        thread_id=thread_id,
        agent_name=settings.voice_agent_name,
    )
