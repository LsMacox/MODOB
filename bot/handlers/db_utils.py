"""Database utility functions shared across modules."""
from __future__ import annotations

from sqlalchemy import select
from ..database import async_session
from ..models import GroupSetting

async def ensure_group(session, chat_id: int) -> GroupSetting:
    """Retrieve group by chat_id or create one if not exists"""
    # Retrieve group by chat_id (not primary key)
    stmt = select(GroupSetting).where(GroupSetting.chat_id == chat_id)
    grp: GroupSetting | None = await session.scalar(stmt)
    if not grp:
        grp = GroupSetting(chat_id=chat_id)
        session.add(grp)
        await session.flush()  # Use flush to assign an ID to grp without committing
    return grp
