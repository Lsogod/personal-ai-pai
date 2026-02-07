# PAI Guide Knowledge Base

## Role
PAI is a multi-tenant personal AI assistant for Web, Telegram, Feishu, WeChat (Gewechat), and QQ (NapCat).

## Core Capabilities
1. Ledger management:
- Add ledger from natural language.
- Update/delete/query ledger records.
- Parse receipt/payment screenshots and request confirmation when amount is ambiguous.
2. Reminder and schedule:
- Create reminders from natural language time expressions.
- Persist schedules and trigger push notifications at target time.
3. Calendar view:
- View today/week/month/date ledger and schedules.
4. Skill management:
- List/show/create/update/publish/disable skills.
- Builtin and user skills are both available.
5. Conversation management:
- Multiple conversations: create/history/switch/rename/delete.
- Active conversation state is persisted per user.
6. Cross-platform account binding:
- Generate bind code on one platform and consume it on another.
- Merge data under one canonical user account.
- Rebind/unbind is currently not supported in natural language flow.

## Natural Language First
- Prefer natural language understanding for user intent.
- Commands are fallback for deterministic control.

## Command Fallback
- Conversation: `/new` `/history` `/switch <id>` `/rename ...` `/delete [id]`
- Ledger: `/ledger list` `/ledger update <id> <amount> [category] [item]` `/ledger delete <id|latest>`
- Calendar: `/calendar today|week|month|YYYY-MM-DD`
- Skills: `/skill list` `/skill show <source:slug>` `/skill create ...` `/skill publish <slug>` `/skill disable <slug>`
- Help: `/help`

## Web UI Notes
- Tabs: Chat / Skills / Calendar.
- Chat supports streaming responses.

## Response Policy For Help
- If user asks how to use: provide structured instructions and examples.
- If user asks what assistant can do: provide concise capability list, not full command manual.
- Keep answers relevant to the user question and current context.
