# Personal AI (PAI) v1.0

中心化多用户智能助理（多平台 + 独立 Web 客户端）。

## 快速开始

1. 复制环境变量

```bash
cp .env.example .env
```

2. 启动服务

```bash
docker compose up --build
```

3. 打开 Web 客户端

浏览器访问 `http://localhost:3001/`。

后端 API 端口：`http://localhost:8000/`。

提示：当前后端镜像默认使用单 worker 以确保 APScheduler 持久化任务稳定运行。

## 项目结构

```text
backend/   FastAPI + LangGraph + SQLModel + APScheduler
frontend/  React18 + Vite + TypeScript + Tailwind + shadcn/ui
```

## Webhook

- `POST /webhook/telegram`
- `POST /webhook/wechat`
- `POST /webhook/qq`
- `POST /webhook/feishu`

### 平台接入说明

- Telegram: 配置 `TELEGRAM_BOT_TOKEN` 与 `TELEGRAM_WEBHOOK_SECRET`，并在 Telegram 侧设置 webhook 到 `/webhook/telegram`。
- Telegram 本地无 HTTPS 时，可启用轮询：设置 `TELEGRAM_POLLING_ENABLED=true`，并确保 Telegram webhook 已删除（`deleteWebhook`）。
- 飞书: 配置 `FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_VERIFICATION_TOKEN`，事件订阅指向 `/webhook/feishu`（当前按未加密事件处理）。
- QQ (NapCat / OneBot v11): NapCat 配置 HTTP POST 到 `/webhook/qq`，发送接口走 `ONEBOT_BASE_URL`。
- 微信 (Gewechat): Gewechat 回调指向 `/webhook/wechat`，发送接口默认 `GEWECHAT_BASE_URL/sendText` 与 `GEWECHAT_BASE_URL/sendImage`，如接口不同请调整代码或反向代理适配。

payload 至少包含：

```json
{
  "platform_id": "wxid_123",
  "content": "记账 35 午餐",
  "image_urls": ["https://example.com/receipt.jpg"],
  "message_id": "msg-1",
  "event_ts": 1738800000
}
```

## Web 客户端 API

### 认证

- `POST /api/auth/register`
- `POST /api/auth/login`

### 业务

- `POST /api/chat/send`
- `GET /api/chat/ws?token=<JWT>` (WebSocket)
- `GET /api/chat/history`
- `GET /api/user/profile`
- `GET /api/stats/ledger`

`/api/chat/send` 支持：

- 普通响应：默认 JSON 返回
- 流式响应：`/api/chat/send?stream=true`，SSE 输出 `data: ...`

默认使用 Redis Checkpointer 持久化（compose 已切换为 `redis-stack`）。
仅在本地排障时才建议临时设置 `ALLOW_MEMORY_CHECKPOINTER_FALLBACK=true`。

## 管理 API

- `GET /api/users`
- `GET /api/ledgers`
- `GET /api/schedules`
- `GET /api/audit`

管理 API 需要 `X-Admin-Token` 头，与 `.env` 中 `ADMIN_TOKEN` 一致。

## 前端开发

```bash
cd frontend
npm install
npm run dev
```

开发模式默认代理 `/api` 到 `http://localhost:8000`。
