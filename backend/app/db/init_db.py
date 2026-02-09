from sqlalchemy import text
from sqlmodel import SQLModel

from app.db.session import engine
from app.models.user import User
from app.models.identity import UserIdentity
from app.models.bind_code import BindCode
from app.models.ledger import Ledger
from app.models.schedule import Schedule
from app.models.audit import AuditLog
from app.models.message import Message
from app.models.conversation import Conversation
from app.models.skill import Skill, SkillVersion
from app.models.reminder_delivery import ReminderDelivery
from app.models.memory import LongTermMemory


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS hashed_password VARCHAR"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS active_conversation_id INTEGER"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS binding_stage INTEGER"))
            await conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS conversation_id INTEGER"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messages_conversation_id ON messages (conversation_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_active_conversation_id ON users (active_conversation_id)"))
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_reminder_deliveries_schedule_id ON reminder_deliveries (schedule_id)")
            )
        except Exception:
            pass
