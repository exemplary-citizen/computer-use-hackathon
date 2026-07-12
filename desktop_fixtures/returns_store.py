"""Deterministic persistence for the Atlas Returns Desk demo fixture."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CASE_STATUSES = ("New", "In Review", "Awaiting Customer", "Escalated", "Resolved")
CUSTOMER_TIERS = ("Standard", "Silver", "Gold", "Enterprise")
RESOLUTION_VALUES = ("Pending Review", "Replacement", "Full Refund", "Partial Refund", "Repair", "Deny Return")
DISPOSITION_VALUES = (
    "UNASSESSED",
    "RETURN_TO_STOCK",
    "REFURBISHMENT",
    "VENDOR_RETURN",
    "HAZMAT_INSPECTION",
    "SCRAP_AUTHORIZED",
)
WAREHOUSE_ROUTES = ("Unassigned", "WH-WEST-01", "WH-EAST-03", "WH-REFURB-01", "WH-HAZ-02")
ASSIGNEES = ("Unassigned", "Maya Chen", "Jon Bell", "Priya Shah", "Lena Ortiz")

CaseStatus = Literal["New", "In Review", "Awaiting Customer", "Escalated", "Resolved"]
CustomerTier = Literal["Standard", "Silver", "Gold", "Enterprise"]
Resolution = Literal["Pending Review", "Replacement", "Full Refund", "Partial Refund", "Repair", "Deny Return"]
Disposition = Literal[
    "UNASSESSED",
    "RETURN_TO_STOCK",
    "REFURBISHMENT",
    "VENDOR_RETURN",
    "HAZMAT_INSPECTION",
    "SCRAP_AUTHORIZED",
]


class TimelineEvent(BaseModel):
    """One immutable business or audit event shown on a return case."""

    model_config = ConfigDict(extra="forbid")

    occurred_at: str
    actor: str
    action: str
    detail: str


class ReturnCase(BaseModel):
    """One returns case containing source facts and the operator decision."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    order_number: str
    customer_name: str
    customer_email: str
    customer_tier: CustomerTier
    product_name: str
    sku: str
    category: str
    serial_number: str
    purchase_date: str
    request_date: str
    sales_channel: str
    return_reason: str
    item_condition: str
    requested_remedy: str
    order_value: float = Field(ge=0)
    status: CaseStatus
    assignee: str
    sla_hours_remaining: int
    risk_flags: list[str]
    policy_hint: str
    resolution: Resolution = "Pending Review"
    disposition_code: Disposition = "UNASSESSED"
    refund_amount: float = Field(default=0, ge=0)
    restocking_fee: float = Field(default=0, ge=0)
    warehouse_route: str = "Unassigned"
    internal_note: str = ""
    timeline: list[TimelineEvent] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)


class ReturnsState(BaseModel):
    """Full persisted state of Atlas Returns Desk."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    last_sync_at: str
    cases: list[ReturnCase]


def returns_state_path(data_root: Path | None = None) -> Path:
    """Return the configured JSON path for Atlas Returns Desk.

    Args:
        data_root: Optional test override for the fixture data directory.

    Returns:
        Path to the Atlas JSON state file.
    """
    root = data_root or Path(os.environ.get("FOUNDRY_FIXTURE_DATA_ROOT", "data/desktop_fixtures"))
    return root / "atlas_returns.json"


def load_returns_state(path: Path) -> ReturnsState:
    """Load Atlas state, creating the deterministic seed when absent.

    Args:
        path: JSON state path.

    Returns:
        Validated returns state.
    """
    if not path.is_file():
        state = default_returns_seed()
        write_returns_state(path, state)
        return state
    return ReturnsState.model_validate_json(path.read_text(encoding="utf-8"))


def write_returns_state(path: Path, state: ReturnsState) -> None:
    """Atomically persist validated Atlas state.

    Args:
        path: Destination JSON state path.
        state: Validated state to persist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as temporary:
        temporary.write(payload)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def reset_returns_state(path: Path) -> ReturnsState:
    """Restore and persist the canonical Atlas seed.

    Args:
        path: Destination JSON state path.

    Returns:
        The restored state.
    """
    state = default_returns_seed()
    write_returns_state(path, state)
    return state


def default_returns_seed() -> ReturnsState:
    """Build the deterministic business dataset used by the demo and tests."""
    cases = [
        _case(
            "RTN-1048",
            "ORD-884219",
            "Avery Morgan",
            "avery@northstar-labs.com",
            "Gold",
            "VoltEdge Portable Power Station 500",
            "VE-PPS-500",
            "Rechargeable Power",
            "VE500-24-01983",
            "2026-06-08",
            "2026-07-10",
            "Web",
            "Battery enclosure became hot and emitted a chemical odor during charging.",
            "Safety concern — powers on intermittently",
            "Replacement",
            489.00,
            "In Review",
            "Maya Chen",
            3,
            ["LITHIUM BATTERY", "SAFETY COMPLAINT", "PREMIUM CUSTOMER"],
            "Policy H-04: isolate damaged rechargeable products; premium window is 45 days.",
            ["customer_video.mov", "shipping_label.pdf", "order_receipt.pdf"],
        ),
        _case(
            "RTN-1057",
            "ORD-885041",
            "Noah Williams",
            "noah@williams.design",
            "Gold",
            "AeroCharge Pro Battery Pack",
            "AC-PRO-220",
            "Rechargeable Power",
            "ACP220-26-44102",
            "2026-06-18",
            "2026-07-12",
            "Retail Store",
            "Unit swelled after two charge cycles; customer stopped using it immediately.",
            "Visible swelling — quarantined by store",
            "Replacement",
            329.50,
            "New",
            "Unassigned",
            7,
            ["LITHIUM BATTERY", "PHYSICAL DAMAGE", "PREMIUM CUSTOMER"],
            "Policy H-04 and P-17 may apply; route confirmed hazards to the isolation facility.",
            ["store_intake.jpg", "purchase_receipt.pdf"],
        ),
        _case(
            "RTN-1061",
            "ORD-885288",
            "Sofia Patel",
            "sofia.patel@example.com",
            "Enterprise",
            "Lumina 34-inch Conference Display",
            "LM-CD-34",
            "Displays",
            "LM34-99201",
            "2026-06-29",
            "2026-07-11",
            "B2B Portal",
            "Display arrived with a vertical line through the panel.",
            "Like new — shipping damage",
            "Replacement",
            899.00,
            "Awaiting Customer",
            "Priya Shah",
            18,
            ["HIGH VALUE", "ENTERPRISE ACCOUNT"],
            "Enterprise DOA replacements receive expedited cross-ship after photo verification.",
            ["panel_photo.png"],
        ),
        _case(
            "RTN-1064",
            "ORD-885431",
            "Ethan Brooks",
            "ethan.brooks@example.com",
            "Standard",
            "TrailForm Carbon Trekking Poles",
            "TF-CTP-2",
            "Outdoor",
            "TF2-18820",
            "2026-05-01",
            "2026-07-09",
            "Marketplace",
            "Customer changed plans and no longer needs the product.",
            "Opened packaging — unused",
            "Full Refund",
            149.99,
            "Escalated",
            "Jon Bell",
            -5,
            ["OUTSIDE STANDARD WINDOW", "MARKETPLACE ORDER"],
            "Marketplace returns outside 30 days require supervisor review.",
            ["marketplace_order.pdf"],
        ),
        _case(
            "RTN-1068",
            "ORD-885774",
            "Liam Chen",
            "liam.chen@example.com",
            "Silver",
            "Sonora Noise-Canceling Headphones",
            "SN-NC-9",
            "Audio",
            "SN9-77200",
            "2026-06-23",
            "2026-07-08",
            "Web",
            "Left channel cuts out when the headband is adjusted.",
            "Good — intermittent defect",
            "Repair",
            219.00,
            "In Review",
            "Lena Ortiz",
            11,
            ["ELECTRONICS", "WARRANTY ELIGIBLE"],
            "Attempt depot repair before replacement for serviceable audio products.",
            ["audio_sample.m4a", "receipt.pdf"],
        ),
        _case(
            "RTN-1072",
            "ORD-886015",
            "Olivia Johnson",
            "olivia.j@example.com",
            "Gold",
            "Caspian Espresso Machine",
            "CS-ESP-12",
            "Kitchen",
            "CSE12-66192",
            "2026-06-02",
            "2026-07-06",
            "Retail Store",
            "Pump does not build pressure after descaling procedure.",
            "Used — clean",
            "Replacement",
            549.00,
            "New",
            "Unassigned",
            22,
            ["PREMIUM CUSTOMER", "HEAVY ITEM"],
            "Premium members qualify for replacement within 45 days when troubleshooting is complete.",
            ["service_log.pdf"],
        ),
        _case(
            "RTN-1075",
            "ORD-886119",
            "Mason Reed",
            "mason.reed@example.com",
            "Standard",
            "Kestrel Smart Doorbell",
            "KS-DB-4",
            "Smart Home",
            "KDB4-22911",
            "2026-06-15",
            "2026-07-11",
            "Web",
            "Device disconnects from Wi-Fi several times per day.",
            "Installed — cosmetic wear",
            "Replacement",
            179.95,
            "New",
            "Unassigned",
            26,
            ["CONNECTED DEVICE"],
            "Confirm firmware version before authorizing connected-device replacement.",
            ["diagnostic_log.txt"],
        ),
        _case(
            "RTN-1033",
            "ORD-882771",
            "Emma Davis",
            "emma.davis@example.com",
            "Enterprise",
            "ArborFlex Ergonomic Chair",
            "AF-ERG-8",
            "Office",
            "AFE8-11887",
            "2026-05-27",
            "2026-06-04",
            "B2B Portal",
            "Seat lift cylinder failed during normal use.",
            "Used — mechanical failure",
            "Replacement",
            699.00,
            "Resolved",
            "Priya Shah",
            0,
            ["ENTERPRISE ACCOUNT", "WORKPLACE SAFETY"],
            "Resolved cases are read-only; reopen requires supervisor permission.",
            ["failure_photo.jpg", "resolution_letter.pdf"],
            resolution="Replacement",
            disposition="VENDOR_RETURN",
            route="WH-EAST-03",
        ),
    ]
    return ReturnsState(schema_version="1.0", last_sync_at="2026-07-12T10:00:00Z", cases=cases)


def _case(
    case_id: str,
    order_number: str,
    customer_name: str,
    customer_email: str,
    customer_tier: CustomerTier,
    product_name: str,
    sku: str,
    category: str,
    serial_number: str,
    purchase_date: str,
    request_date: str,
    sales_channel: str,
    return_reason: str,
    item_condition: str,
    requested_remedy: str,
    order_value: float,
    status: CaseStatus,
    assignee: str,
    sla_hours_remaining: int,
    risk_flags: list[str],
    policy_hint: str,
    attachments: list[str],
    *,
    resolution: Resolution = "Pending Review",
    disposition: Disposition = "UNASSESSED",
    route: str = "Unassigned",
) -> ReturnCase:
    opened = TimelineEvent(
        occurred_at=f"{request_date}T09:14:00Z",
        actor="Atlas Intake",
        action="Case created",
        detail=f"Return request imported from {sales_channel}; requested remedy: {requested_remedy}.",
    )
    events = [opened]
    if assignee != "Unassigned":
        events.append(
            TimelineEvent(
                occurred_at=f"{request_date}T10:02:00Z",
                actor="Queue Router",
                action="Case assigned",
                detail=f"Assigned to {assignee} based on category and workload.",
            )
        )
    if status == "Resolved":
        events.append(
            TimelineEvent(
                occurred_at="2026-06-04T16:22:00Z",
                actor=assignee,
                action="Resolution applied",
                detail=f"{resolution}; disposition {disposition}; routed to {route}.",
            )
        )
    return ReturnCase(
        case_id=case_id,
        order_number=order_number,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_tier=customer_tier,
        product_name=product_name,
        sku=sku,
        category=category,
        serial_number=serial_number,
        purchase_date=purchase_date,
        request_date=request_date,
        sales_channel=sales_channel,
        return_reason=return_reason,
        item_condition=item_condition,
        requested_remedy=requested_remedy,
        order_value=order_value,
        status=status,
        assignee=assignee,
        sla_hours_remaining=sla_hours_remaining,
        risk_flags=risk_flags,
        policy_hint=policy_hint,
        resolution=resolution,
        disposition_code=disposition,
        refund_amount=order_value if resolution == "Full Refund" else 0,
        restocking_fee=0,
        warehouse_route=route,
        timeline=events,
        attachments=attachments,
    )


def now_timestamp() -> str:
    """Return a stable UTC ISO timestamp for newly persisted audit events."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
