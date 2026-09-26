"""Live shop-assistant chat (``/ai/chat``, ``/ai/chat/resume``).

Runs the master Deep Agent on the xKiro-backed model. Step 2 attaches
tenant-bound business read tools (real shop data, read-only) built from
the request's DB session and authenticated tenant — the model never
supplies ``shop_id``. Step 3 additionally attaches the single
tenant-bound sale write tool (``create_sale``), Step 4 the single
tenant-bound customer-payment write tool (``record_customer_payment``),
and Step 5 the single tenant-bound supplier-payment write tool
(``record_supplier_payment``); each pauses with an interrupt, and the
frontend approves/rejects and resumes.
Conversation state lives in a process-local checkpointer keyed by a
thread id that is always namespaced with the authenticated shop + user,
so one tenant can never resume another's thread.

Transaction ownership: the write tools only flush — this module commits
after a finished (``done``) agent run, so an approved sale (rows + stock
+ payments + ledger + idempotency receipt) or an approved customer
payment (payment + ledger + idempotency receipt) commits atomically. A
run that pauses for approval is left untouched: the interrupt fires
BEFORE the tool executes, so nothing was mutated and there is nothing
to undo. No transaction ever spans the HITL pause: each request builds
the agent over its own fresh session.

Authorization: any authenticated member of the shop (owner or staff) may
record sales/payments through the assistant — exactly the same rule as
``POST /sales``, ``POST /customers/{id}/payments`` and
``POST /suppliers/{id}/payments``, which require authentication + shop
scope and no owner role. No new authorization system is introduced here.

Every route requires authentication; tenant context always comes from
the verified Clerk token via ``CurrentUserDep``.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.errors import InvalidUpdateError
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.ai.agent import build_master_agent, create_checkpointer, resolve_model
from app.ai.llm import MissingXKiroKeyError
from app.ai.state import tenant_context_from_user
from app.ai.tools.business_reads import build_read_tools
from app.ai.tools.payments_write import build_payment_write_tools
from app.ai.tools.sales_write import build_sale_write_tools
from app.ai.tools.supplier_payments_write import build_supplier_payment_write_tools
from app.api.dependencies import CurrentUserDep, DbSession
from app.models.user import User

router = APIRouter(prefix="/ai", tags=["ai"])

ALLOWED_DECISION_TYPES = ("approve", "edit", "reject", "respond")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = Field(default=None, max_length=64)


class HitlDecision(BaseModel):
    type: str
    message: str | None = None
    edited_action: dict[str, Any] | None = None


class ChatResumeRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)
    decisions: list[HitlDecision] = Field(min_length=1, max_length=10)


_shared_checkpointer: Any = None


def get_shared_checkpointer() -> Any:
    """Process-local checkpointer holding chat threads.

    Restarting the server drops conversations; a persistent (Postgres)
    checkpointer is a later step.
    """
    global _shared_checkpointer
    if _shared_checkpointer is None:
        _shared_checkpointer = create_checkpointer()
    return _shared_checkpointer


async def get_chat_model() -> Any:
    """xKiro-backed chat model (overridable in tests with a fake)."""
    try:
        return resolve_model("auto")
    except MissingXKiroKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


ChatModelDep = Annotated[Any, Depends(get_chat_model)]


def _namespaced_thread(user: User, thread_id: str | None) -> tuple[str, str]:
    """Namespace a client thread id with the authenticated shop + user.

    Returns ``(client_thread_id, namespaced_thread_id)``. The client only
    ever sees the opaque id; the graph thread always carries the tenant,
    so a forged id can never cross into another shop's conversation.
    """
    client_id = thread_id or uuid.uuid4().hex
    namespaced = f"{user.shop_id}:{user.id}:{client_id}"
    return client_id, namespaced


def _extract_reply(result: Any) -> str:
    """Last assistant message text from a ``version="v2"`` invoke result."""
    value = getattr(result, "value", result)
    if isinstance(value, dict):
        messages = value.get("messages") or []
        if messages:
            content = getattr(messages[-1], "content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict)
                )
    return ""


def _serialize_interrupts(interrupts: Any) -> list[dict[str, Any]]:
    """Pending HITL actions in frontend-friendly shape."""
    value = interrupts[0].value
    action_requests = value.get("action_requests", [])
    configs = {
        config.get("action_name"): config
        for config in value.get("review_configs", [])
    }
    pending: list[dict[str, Any]] = []
    for action in action_requests:
        config = configs.get(action.get("name"), {})
        pending.append(
            {
                "name": action.get("name"),
                "args": action.get("args", {}),
                "allowed_decisions": config.get(
                    "allowed_decisions",
                    ["approve", "edit", "reject", "respond"],
                ),
            }
        )
    return pending


def _validate_decisions(decisions: list[HitlDecision]) -> list[dict[str, Any]]:
    """Validate resume decisions; 422 on any malformed entry."""
    payload: list[dict[str, Any]] = []
    for decision in decisions:
        if decision.type not in ALLOWED_DECISION_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown decision type {decision.type!r}; "
                f"expected one of {list(ALLOWED_DECISION_TYPES)}.",
            )
        entry: dict[str, Any] = {"type": decision.type}
        if decision.type in ("reject", "respond"):
            if not (decision.message or "").strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"A {decision.type!r} decision needs a message "
                    "explaining what to do instead.",
                )
            entry["message"] = decision.message
        if decision.type == "edit":
            if not isinstance(decision.edited_action, dict) or not isinstance(
                decision.edited_action.get("args"), dict
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="An 'edit' decision needs edited_action "
                    "{name, args} with args as an object.",
                )
            entry["edited_action"] = decision.edited_action
        elif decision.message:
            entry["message"] = decision.message
        payload.append(entry)
    return payload


def _chat_response(
    result: Any, client_thread_id: str, fallback_notice: str = ""
) -> dict[str, Any]:
    """Shape an invoke result as done/paused for the frontend."""
    interrupts = list(getattr(result, "interrupts", None) or [])
    if interrupts:
        return {
            "status": "paused",
            "thread_id": client_thread_id,
            "interrupts": _serialize_interrupts(interrupts),
        }
    reply = _extract_reply(result) or fallback_notice
    return {
        "status": "done",
        "thread_id": client_thread_id,
        "reply": reply,
    }


@router.post("/chat")
async def ai_chat(
    body: ChatRequest,
    current_user: CurrentUserDep,
    model: ChatModelDep,
    db: DbSession,
) -> dict[str, Any]:
    """Send a message to the shop assistant; returns reply or approval pause."""
    tenant = tenant_context_from_user(current_user)
    client_thread_id, namespaced = _namespaced_thread(current_user, body.thread_id)
    read_tools = build_read_tools(db, tenant)
    write_tools = [
        *build_sale_write_tools(db, tenant),
        *build_payment_write_tools(db, tenant),
        *build_supplier_payment_write_tools(db, tenant),
    ]
    agent = build_master_agent(
        model=model,
        checkpointer=get_shared_checkpointer(),
        extra_tools=read_tools,
        write_tools=write_tools,
    )
    first_message = (
        f"[tenant shop_id={tenant.shop_id} user_id={tenant.user_id}] {body.message}"
    )
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": first_message}]},
            config={"configurable": {"thread_id": namespaced}},
            version="v2",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI provider error: {exc}",
        ) from exc
    response = _chat_response(result, client_thread_id)
    await _settle_transaction(db, response["status"])
    return response


@router.post("/chat/resume")
async def ai_chat_resume(
    body: ChatResumeRequest,
    current_user: CurrentUserDep,
    model: ChatModelDep,
    db: DbSession,
) -> dict[str, Any]:
    """Resume a paused chat with human decisions for every pending action."""
    decisions = _validate_decisions(body.decisions)
    tenant = tenant_context_from_user(current_user)
    _, namespaced = _namespaced_thread(current_user, body.thread_id)
    read_tools = build_read_tools(db, tenant)
    write_tools = [
        *build_sale_write_tools(db, tenant),
        *build_payment_write_tools(db, tenant),
        *build_supplier_payment_write_tools(db, tenant),
    ]
    agent = build_master_agent(
        model=model,
        checkpointer=get_shared_checkpointer(),
        extra_tools=read_tools,
        write_tools=write_tools,
    )
    try:
        result = await agent.ainvoke(
            Command(resume={"decisions": decisions}),
            config={"configurable": {"thread_id": namespaced}},
            version="v2",
        )
    except InvalidUpdateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No pending approval on this conversation.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI provider error: {exc}",
        ) from exc
    response = _chat_response(result, body.thread_id)
    await _settle_transaction(db, response["status"])
    return response


async def _settle_transaction(db: DbSession, run_status: str) -> None:
    """Commit a finished run; leave a paused run untouched.

    The write tools flush but never commit, so the commit here is what
    makes an approved operation durable — a sale (rows + stock + payments
    + ledger + idempotency receipt), a customer payment (payment +
    ledger + idempotency receipt), or a supplier payment (payment +
    ledger + idempotency receipt) together, or nothing at all. A
    ``paused`` run performed no approved mutation (the interrupt fires
    before the tool executes), so its session is deliberately left alone.
    """
    if run_status != "done":
        return
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI provider error: {exc}",
        ) from exc
