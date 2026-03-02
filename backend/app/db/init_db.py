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
from app.models.admin_tool import AdminToolSwitch
from app.models.llm_usage import LLMUsageLog
from app.models.tool_usage import ToolUsageLog
from app.models.feedback import UserFeedback
from app.models.app_setting import AppSetting


async def init_db() -> None:
    async with engine.begin() as conn:
        # Avoid startup hangs when another process holds table locks (e.g. memory worker queries).
        await conn.execute(text("SET LOCAL lock_timeout = '3s'"))
        await conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        await conn.run_sync(SQLModel.metadata.create_all)
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS hashed_password VARCHAR"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS active_conversation_id INTEGER"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS binding_stage INTEGER"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS blocked_reason VARCHAR"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_message_limit INTEGER DEFAULT 30"))
            await conn.execute(text("ALTER TABLE users ALTER COLUMN daily_message_limit SET DEFAULT 30"))
            await conn.execute(text("UPDATE users SET daily_message_limit = 30 WHERE daily_message_limit IS NULL"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS monthly_message_limit INTEGER DEFAULT 0"))
            await conn.execute(text("ALTER TABLE users ALTER COLUMN monthly_message_limit SET DEFAULT 0"))
            await conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS conversation_id INTEGER"))
            await conn.execute(
                text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS memory_extracted_at TIMESTAMPTZ")
            )
            await conn.execute(
                text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS memory_last_processed_message_id INTEGER")
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_conversations_memory_last_processed_message_id "
                    "ON conversations (memory_last_processed_message_id)"
                )
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messages_conversation_id ON messages (conversation_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_active_conversation_id ON users (active_conversation_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_is_blocked ON users (is_blocked)"))
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_reminder_deliveries_schedule_id ON reminder_deliveries (schedule_id)")
            )
        except Exception:
            pass
