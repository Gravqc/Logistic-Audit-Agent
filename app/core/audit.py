from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import AuditLog

async def log_audit_event(
    session: AsyncSession,
    event_type: str,
    freight_bill_id: str = None,
    event_detail: dict = None
):
    audit_entry = AuditLog(
        freight_bill_id=freight_bill_id,
        event_type=event_type,
        event_detail=event_detail or {}
    )
    session.add(audit_entry)
    await session.commit()
