"""Master Deep Agent foundation (Step 1).

Uses the current documented ``create_deep_agent`` API
(deepagents 0.7.x, verified against the installed 0.7.19):

* ``create_deep_agent(model=..., tools=..., system_prompt=...,
  subagents=..., interrupt_on=..., checkpointer=...)``
* Human-in-the-loop via ``interrupt_on={"<tool>": True}`` plus a
  LangGraph checkpointer (``MemorySaver``), resumed with
  ``Command(resume={"decisions": [...]})`` on the same ``thread_id``
  with ``version="v2"``.
* Skills via ``SKILL.md`` progressive disclosure (see
  ``app/ai/skills/kapraos-core/SKILL.md``), loaded from disk through
  ``FilesystemBackend`` and the ``skills=[...]`` argument — exactly the
  documented pattern.
* One custom ``analytics`` subagent spec (read-only boundary).

Conceptual flow::

    Understand request -> Determine intent -> Use relevant skill
        -> Choose capability -> Read / prepare / execute -> Return result
"""

from pathlib import Path
from typing import Any

from app.ai.config import get_ai_settings
from app.ai.subagents.analytics import build_analytics_subagent_spec
from app.ai.tools.business_reads import READ_TOOL_NAMES
from app.ai.tools.payments_write import RECORD_PAYMENT_TOOL_NAME
from app.ai.tools.registry import demo_side_effect, get_master_tools
from app.ai.tools.sales_write import CREATE_SALE_TOOL_NAME
from app.ai.tools.supplier_payments_write import RECORD_SUPPLIER_PAYMENT_TOOL_NAME

MASTER_SYSTEM_PROMPT = """You are the KapraOS shop assistant. You help shopkeepers operate and understand their business using natural language.

Core rules:
1. Never invent business data.
2. Never bypass authentication.
3. Never change the active shop/tenant; shop_id comes from authenticated context, never from the user or your own output.
4. Never write raw SQL for transactional operations.
5. Never directly mutate the database.
6. Business mutations must go through approved domain tools/services.
7. Read-only analytics may use the analytics subagent (SELECT-only, always filtered by shop_id).
8. Side-effecting operations (EXECUTE) require human approval (HITL) before running.
9. Follow deterministic backend business rules (server-calculated totals, payment mapping, double-entry balance).
10. Never perform accounting manipulation directly.
11. Never expose data belonging to another shop.
12. When information is ambiguous, resolve it through safe read operations or request clarification.
13. Prefer existing backend services over duplicating business logic.

Step 2 read capabilities (real shop data, read-only):
- Answer sales/stock/khata/supplier/purchase/expense/catalog questions with the typed business read tools (get_sales_summary, get_inventory_status, get_customer_account_summary, get_supplier_account_summary, get_purchase_summary, get_expense_summary, get_product_or_catalog_info, get_dashboard_summary).
- Understand fabric-retail language: khata, udhaar, cash, jama, baqi, gaz, meter, thaan, jora, suit, kapra, maal, bikri, kharid, supplier, customer.
- Use concrete UTC date boundaries (YYYY-MM-DD) for ranges like today/yesterday/this week/this month/last month.
- When a tool reports ambiguity (multiple Ahmeds, multiple Black Lawn products), ask the shopkeeper which one they mean instead of guessing.
- Explain results in plain shopkeeper language with Rs. amounts; never describe backend services, SQL, shop_id, or implementation details.
"""

# Master tools are the Step 1 demo registry only (safe, tiny).
# Step 2 read tools are per-request via `extra_tools` (see build_master_agent).
MASTER_TOOL_NAMES: tuple[str, ...] = (
    "kapraos_demo_info",
    "demo_prepare_operation",
    "demo_side_effect",
)

# Step 2 business read tools (re-exported for a single obvious boundary).
READ_MASTER_TOOL_NAMES: tuple[str, ...] = READ_TOOL_NAMES

# Step 3 + Step 4 + Step 5 write tools (exactly three mutations: sale
# creation, customer payment, and supplier payment). Each module still
# exposes exactly one tool; the master agent orchestrates all three
# without any specialised sub-agent.
WRITE_MASTER_TOOL_NAMES: tuple[str, ...] = (
    CREATE_SALE_TOOL_NAME,
    RECORD_PAYMENT_TOOL_NAME,
    RECORD_SUPPLIER_PAYMENT_TOOL_NAME,
)

# HITL: the fake side-effect demo pauses for human review.
# ``True`` = approve / edit / reject / respond allowed (docs default).
# A real checkpointer is REQUIRED for this to pause/resume.
HITL_INTERRUPT_CONFIG: dict[str, bool] = {
    "demo_side_effect": True,
}

# HITL for the single Step 3 mutation. Same documented mechanism as the
# demo (``interrupt_on`` + checkpointer + ``Command(resume=...)`` on the
# same thread with ``version="v2"``). ``True`` keeps all four decisions;
# an edit simply re-runs the tool with the edited args, which the tool
# re-validates against the shop before any mutation.
SALE_HITL_INTERRUPT_CONFIG: dict[str, bool] = {
    CREATE_SALE_TOOL_NAME: True,
}

# HITL for the single Step 4 mutation (customer payment / Khata
# settlement). Identical mechanism: ``True`` keeps approve / edit /
# reject / respond; an edit re-runs ``record_customer_payment`` with the
# edited args, which the tool re-validates (customer, amount, method)
# before any mutation.
PAYMENT_HITL_INTERRUPT_CONFIG: dict[str, bool] = {
    RECORD_PAYMENT_TOOL_NAME: True,
}

# HITL for the single Step 5 mutation (supplier payment / supplier Khata
# settlement). Identical mechanism: ``True`` keeps approve / edit /
# reject / respond; an edit re-runs ``record_supplier_payment`` with the
# edited args, which the tool re-validates (supplier, amount, method)
# before any mutation.
SUPPLIER_PAYMENT_HITL_INTERRUPT_CONFIG: dict[str, bool] = {
    RECORD_SUPPLIER_PAYMENT_TOOL_NAME: True,
}

# Appended to the system prompt ONLY when sale write tools are attached,
# so runs without the mutation capability never learn a tool that is not
# present (and cannot hallucinate sale tool calls).
SALE_SYSTEM_ADDENDUM = """
Sale recording (Step 3 — single mutation `create_sale`, always HITL-approved):
1. Understand: customer name, product name, quantity, price if stated, cash vs udhaar.
2. Resolve with the read tools first (customer account summary, catalog/inventory lookup).
   If a name matches several customers/products/variants, ASK which one — never guess.
3. In the SAME turn: write a short preview to the shopkeeper (customer, product,
   quantity + unit, total, cash/udhaar) AND THEN IMMEDIATELY call `create_sale`.
   Never end your turn after the preview without calling the tool — the tool call
   is what raises the approval card, and without it nothing can be approved.
4. Never ask "should I record it?" / "kya yeh theek hai?" in text and stop. The
   approval card IS the confirmation question; your text preview is only the summary.
5. If the shopkeeper is confirming a preview from the previous turn (haan, theek hai,
   kar do, yes, ok), skip the preview and call `create_sale` directly.
6. Call `create_sale` ONCE per sale with a FRESH idempotency_key (a UUID hex string,
   generated once per new sale request; reuse the same key only when retrying the SAME
   operation). The pending call pauses for the shopkeeper's approval — never claim the
   sale is recorded before the approved result comes back.
7. Pricing: omit unit_price to use the catalog selling price. If the shopkeeper states a
   TOTAL for N units (e.g. '1800 mein 2 meter'), divide to a per-unit price and say so.
8. Units: never convert — quantity is recorded in the variant's own unit. If the
   shopkeeper says 'gaz' but the variant is sold per 'meter', say so in the preview.
9. Payment: cash/nagad = cash (full now); udhaar/khata/baqi = credit (nothing now);
   a stated partial (jama) = cash + paid_amount.
10. After the result: report sale_id/invoice, total, paid/due in plain shopkeeper
    language. On ambiguous/not_found/error results, explain and ask — never invent.
11. Never pass shop_id (no such argument exists), never invent customer/product IDs.
"""

# Appended to the system prompt ONLY when customer-payment write tools are
# attached, so runs without the mutation capability never learn a tool that
# is not present (and cannot hallucinate payment tool calls).
PAYMENT_SYSTEM_ADDENDUM = """
Customer payment recording (Step 4 — single mutation `record_customer_payment`, always HITL-approved):
1. Understand: which customer paid, how much, and how (cash/bank/jazzcash/easypaisa).
   Examples: 'Ali ne 3000 jama karwaye', 'Ahmed ne 5000 cash diye',
   'Bilal ne 2000 khate mein jama karwaye'.
2. Resolve with the read tools first (customer account summary for the outstanding
   balance). If a name matches several customers, ASK which one — never guess.
   Never create a customer for a payment; a missing name is a clarification, not
   a new customer. Payments always need a named customer — never a walk-in.
3. Never invent an amount. If the shopkeeper did not state one ('Ali ne paise jama
   karwaye'), ask 'Kitne paise jama karwaye?' instead of calling the tool.
   No HITL is shown for an incomplete operation.
4. In the SAME turn: write a short preview to the shopkeeper (customer, current
   outstanding, payment amount, method, remaining) AND THEN IMMEDIATELY call
   `record_customer_payment`. Never end your turn after the preview without calling
   the tool — the tool call is what raises the approval card, and without it
   nothing can be approved.
5. Never ask "should I record it?" / "kya yeh theek hai?" in text and stop. The
   approval card IS the confirmation question; your text preview is only the summary.
6. If the shopkeeper is confirming a preview from the previous turn (haan, theek hai,
   kar do, yes, ok), skip the preview and call `record_customer_payment` directly.
7. Call `record_customer_payment` ONCE per payment with a FRESH idempotency_key (a
   UUID hex string, generated once per new payment request; reuse the same key only
   when retrying the SAME operation). The pending call pauses for the shopkeeper's
   approval — never claim the payment is recorded before the approved result comes back.
8. Payment methods map to cash, card, bank, jazzcash, easypaisa, other
   (naqd/nagad = cash; bank transfer = bank). An omitted method defaults to cash
   and is always shown on the approval card for correction.
9. Do NOT block a payment only because the preview suggests overpayment: the
   authoritative service decides. Report overpayment errors truthfully instead of
   inventing credit/advance rules.
10. After the result: report payment_id, amount, and remaining balance in plain
    shopkeeper language. On ambiguous/not_found/error results, explain and ask —
    never invent.
11. Never pass shop_id (no such argument exists), never invent customer IDs.
"""

# Appended to the system prompt ONLY when supplier-payment write tools are
# attached, so runs without the mutation capability never learn a tool that
# is not present (and cannot hallucinate supplier-payment tool calls).
SUPPLIER_PAYMENT_SYSTEM_ADDENDUM = """
Supplier payment recording (Step 5 — single mutation `record_supplier_payment`, always HITL-approved):
1. Understand: which supplier was paid, how much, and how (cash/bank/jazzcash/easypaisa).
   Examples: 'Bilal supplier ko 5000 de diye', 'Ahmed supplier ko 10 hazar bank
   transfer kiye', 'Bilal ko 3000 jazzcash se diye', 'Bilal ko 5 hazar cash diye'.
   Amounts may use scale words: '5 hazar' = 5000, '10 hazar' = 10000, '2 lakh' = 200000.
2. Resolve with the read tools first (supplier account summary for the outstanding
   payable). If a name matches several suppliers, ASK which one — never guess.
   Never create a supplier for a payment; a missing name is a clarification, not
   a new supplier. Supplier payments always need a named supplier.
3. Never invent an amount. If the shopkeeper did not state one ('Bilal supplier ko
   payment kar do'), ask 'Kitne paise diye?' instead of calling the tool.
   No HITL is shown for an incomplete operation.
4. In the SAME turn: write a short preview to the shopkeeper (supplier, current
   payable, payment amount, method, remaining) AND THEN IMMEDIATELY call
   `record_supplier_payment`. Never end your turn after the preview without calling
   the tool — the tool call is what raises the approval card, and without it
   nothing can be approved.
5. Never ask "should I record it?" / "kya yeh theek hai?" in text and stop. The
   approval card IS the confirmation question; your text preview is only the summary.
6. If the shopkeeper is confirming a preview from the previous turn (haan, theek hai,
   kar do, yes, ok), skip the preview and call `record_supplier_payment` directly.
7. Call `record_supplier_payment` ONCE per payment with a FRESH idempotency_key (a
   UUID hex string, generated once per new payment request; reuse the same key only
   when retrying the SAME operation). The pending call pauses for the shopkeeper's
   approval — never claim the payment is recorded before the approved result comes back.
8. Payment methods map to cash, card, bank, jazzcash, easypaisa, other
   (naqd/nagad = cash; bank transfer = bank). An omitted method defaults to cash
   and is always shown on the approval card for correction.
9. Do NOT block a payment only because the preview suggests overpayment: the
   authoritative service decides. Report overpayment errors truthfully instead of
   inventing credit/advance rules. V1 has no supplier advance workflow.
10. Distinguish customer vs supplier intent: 'Ali ne 3000 jama karwaye' is a CUSTOMER
    payment (money IN); 'Bilal supplier ko 3000 diye' is a SUPPLIER payment (money OUT).
    If the intent is genuinely ambiguous ('Bilal ko 5000' with both a customer and a
    supplier named Bilal), ASK for clarification rather than guessing.
11. After the result: report payment_id, amount, and remaining payable in plain
    shopkeeper language. On ambiguous/not_found/error results, explain and ask —
    never invent.
12. Never pass shop_id (no such argument exists), never invent supplier IDs.
"""

# Allowed resume decision types (per HITL docs).
HITL_ALLOWED_DECISIONS: tuple[str, ...] = ("approve", "edit", "reject", "respond")

# Backend root for skill loading: this ``app/ai`` directory, resolved from
# ``__file__`` so skill loading never depends on the process CWD.
AI_ROOT = Path(__file__).resolve().parent

# Skill source paths passed to ``create_deep_agent(skills=[...])``.
# Per docs these use forward slashes and are relative to the backend root:
# ``/skills/`` resolves to ``<AI_ROOT>/skills/``, which contains one skill
# directory per skill (``kapraos-core/SKILL.md``).
SKILLS_SOURCE_PATHS: list[str] = ["/skills/"]


def create_backend() -> Any:
    """Create the ``FilesystemBackend`` used for skill loading.

    Skills already on disk under the backend root load without uploading;
    ``virtual_mode=True`` sandboxes all backend paths under ``AI_ROOT``.
    """
    from deepagents.backends.filesystem import FilesystemBackend

    return FilesystemBackend(root_dir=str(AI_ROOT), virtual_mode=True)


def create_checkpointer() -> Any:
    """Create the LangGraph checkpointer required for HITL.

    ``MemorySaver`` (in-memory) is correct for Step 1 / local dev; LangSmith
    deployments configure a persistent checkpointer automatically.
    """
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


def default_model_name() -> str:
    """Configured model identifier (``provider:model`` form)."""
    return get_ai_settings().ai_model


def resolve_model(model: Any) -> Any:
    """Resolve the ``model`` argument of :func:`build_master_agent`.

    ``"auto"`` builds the xKiro-backed chat model from settings
    (``XKIRO_API_KEY`` in ``backend/.env``); anything else is passed
    through unchanged (model instance, ``"provider:model"`` string, or
    ``None`` for offline verification).
    """
    from app.ai.llm import build_xkiro_llm

    if model == "auto":
        return build_xkiro_llm()
    return model




def build_master_agent(
    *,
    model: Any = None,
    checkpointer: Any = "auto",
    backend: Any = "auto",
    include_hitl_demo: bool = True,
    extra_tools: list[Any] | None = None,
    write_tools: list[Any] | None = None,
) -> Any:
    """Construct the master Deep Agent using the current supported API.

    Args:
        model: A LangChain chat model instance, ``"auto"`` (xKiro model
            from settings — needs ``XKIRO_API_KEY``), a
            ``"provider:model"`` string (requires that provider package +
            credentials at invoke time), or ``None``. ``None`` builds
            offline for architecture verification (deprecated upstream,
            fine for Step 1 tests); pass a fake model or a real model in
            production. Note the model must support ``bind_tools`` for
            ``invoke()`` to run.
        checkpointer: LangGraph checkpointer. ``"auto"`` creates a
            :func:`create_checkpointer` when HITL is enabled, ``None``
            disables persistence (HITL then cannot pause — only use with
            ``include_hitl_demo=False``).
        backend: Filesystem backend for skill loading. ``"auto"`` creates
            one via :func:`create_backend` rooted at ``app/ai``.
        include_hitl_demo: Attach the harmless ``demo_side_effect`` tool
            with ``interrupt_on`` approval. Always True in Step 1 unless
            the caller explicitly opts out.
        extra_tools: Step 2 tenant-bound business read tools
            (``build_read_tools(session, tenant)``). Appended after the
            demo tools; read tools never require HITL approval.
        write_tools: Step 3 + Step 4 + Step 5 tenant-bound business write
            tools (``build_sale_write_tools(session, tenant)`` +
            ``build_payment_write_tools(session, tenant)`` +
            ``build_supplier_payment_write_tools(session, tenant)`` —
            exactly one tool per module, ``create_sale``,
            ``record_customer_payment`` and ``record_supplier_payment``).
            Appended last; every write tool
            pauses for HITL approval via ``interrupt_on`` and therefore
            requires a checkpointer. Each request rebuilds these over
            its own DB session, so no transaction ever spans the pause.

    Returns the compiled LangGraph ``CompiledStateGraph``.
    """
    from deepagents import create_deep_agent

    resolved_model = resolve_model(model)
    tools = get_master_tools()
    if not include_hitl_demo:
        tools = [t for t in tools if t.name != demo_side_effect.name]
    if extra_tools:
        tools = [*tools, *extra_tools]
    if write_tools:
        tools = [*tools, *write_tools]

    interrupt_on: dict[str, Any] | None = None
    resolved_checkpointer: Any = None
    needs_hitl = include_hitl_demo or bool(write_tools)
    if include_hitl_demo:
        interrupt_on = dict(HITL_INTERRUPT_CONFIG)
    if write_tools:
        interrupt_on = {
            **(interrupt_on or {}),
            **{t.name: True for t in write_tools},
        }
    if needs_hitl:
        resolved_checkpointer = (
            create_checkpointer() if checkpointer == "auto" else checkpointer
        )
        if resolved_checkpointer is None:
            raise ValueError(
                "HITL requires a checkpointer: pass one or leave "
                "'checkpointer=\"auto\"' to create an in-memory saver."
            )
    elif checkpointer not in (None, "auto"):
        resolved_checkpointer = checkpointer

    resolved_backend = create_backend() if backend == "auto" else backend

    system_prompt = MASTER_SYSTEM_PROMPT
    if write_tools:
        present = {getattr(t, "name", "") for t in write_tools}
        if CREATE_SALE_TOOL_NAME in present:
            system_prompt = system_prompt + SALE_SYSTEM_ADDENDUM
        if RECORD_PAYMENT_TOOL_NAME in present:
            system_prompt = system_prompt + PAYMENT_SYSTEM_ADDENDUM
        if RECORD_SUPPLIER_PAYMENT_TOOL_NAME in present:
            system_prompt = system_prompt + SUPPLIER_PAYMENT_SYSTEM_ADDENDUM

    return create_deep_agent(
        model=resolved_model,
        tools=tools,
        system_prompt=system_prompt,
        subagents=[build_analytics_subagent_spec()],
        backend=resolved_backend,
        skills=list(SKILLS_SOURCE_PATHS),
        interrupt_on=interrupt_on,
        checkpointer=resolved_checkpointer,
    )


def approve_decision() -> dict[str, str]:
    """HITL resume decision approving the pending demo tool call."""
    return {"type": "approve"}


def reject_decision(message: str) -> dict[str, str]:
    """HITL resume decision rejecting the pending demo tool call.

    ``message`` must tell the agent the tool was NOT executed and what to
    do instead (per docs: never omit it for side-effecting tools).
    """
    return {"type": "reject", "message": message}


def hitl_resume_payload(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the ``Command(resume=...)`` payload for resuming an interrupt.

    Usage (per current HITL docs)::

        from langgraph.types import Command
        agent.invoke(
            Command(resume=hitl_resume_payload(decisions)),
            config={"configurable": {"thread_id": thread_id}},
            version="v2",
        )
    """
    return {"decisions": decisions}


__all__ = [
    "AI_ROOT",
    "HITL_ALLOWED_DECISIONS",
    "HITL_INTERRUPT_CONFIG",
    "MASTER_SYSTEM_PROMPT",
    "MASTER_TOOL_NAMES",
    "PAYMENT_HITL_INTERRUPT_CONFIG",
    "PAYMENT_SYSTEM_ADDENDUM",
    "READ_MASTER_TOOL_NAMES",
    "SALE_HITL_INTERRUPT_CONFIG",
    "SALE_SYSTEM_ADDENDUM",
    "SKILLS_SOURCE_PATHS",
    "SUPPLIER_PAYMENT_HITL_INTERRUPT_CONFIG",
    "SUPPLIER_PAYMENT_SYSTEM_ADDENDUM",
    "WRITE_MASTER_TOOL_NAMES",
    "approve_decision",
    "build_master_agent",
    "create_backend",
    "create_checkpointer",
    "default_model_name",
    "hitl_resume_payload",
    "reject_decision",
    "resolve_model",
]
