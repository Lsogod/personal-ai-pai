# PAI 小程序客户端（完整可用版）

本文档给出当前代码库内可直接落地的完整方案，目标不是 MVP，而是可上线可维护。

## 1. 总体目标

- 新增小程序客户端，和 Web/Telegram/飞书/微信/QQ 共享同一份用户数据。
- 提醒能力统一走后端调度，支持多端广播：
  - IM 端文本推送
  - Web 端实时通知（WS）
  - 小程序端在线实时通知（WS）+ 离线订阅消息（模板消息）
- 提醒投递可观测：每个目标端都有独立投递记录与状态。

## 2. 已实现接口与能力

### 2.1 小程序登录

- `POST /api/miniapp/auth/login`
- 请求：

```json
{
  "code": "wx.login() 返回的 code",
  "nickname": "可选"
}
```

- 返回：

```json
{
  "access_token": "JWT",
  "token_type": "bearer",
  "openid": "wx_openid"
}
```

说明：
- 后端会调用微信 `jscode2session` 换取 `openid`。
- 用户主键统一为 PAI 的 `users.id`，`openid` 写入 `user_identities(platform=miniapp)`。

### 2.2 小程序聊天

- 使用现有 `POST /api/chat/send`
- 新增字段：`source_platform`（可选，`web`/`miniapp`）

```json
{
  "content": "30秒后提醒我测试",
  "image_urls": [],
  "source_platform": "miniapp"
}
```

说明：
- `source_platform=miniapp` 时，后端会优先使用该用户已绑定的 `miniapp` 身份。
- 同一用户在多端共享会话、账单、日程与技能数据。

### 2.3 提醒多端分发与记录

新增模型：`reminder_deliveries`
- 粒度：`schedule_id + platform + platform_id`
- 状态：`PENDING / SENDING / SENT / FAILED`
- 字段：尝试次数、最后错误、送达时间

新增服务：`app/services/reminder_dispatcher.py`
- 汇总用户全部绑定身份
- 去重后逐目标投递
- 每目标最多重试 3 次（0s, 1s, 3s）
- 记录独立投递状态

调度任务：`app/services/scheduler_tasks.py`
- 到点触发后调用 dispatcher
- 全部失败：`Schedule.status=FAILED`
- 有成功：`Schedule.status=EXECUTED`

### 2.4 重启恢复

启动时自动恢复未执行提醒：
- `main.py` startup -> `restore_pending_reminder_jobs()`
- 扫描 `schedules.status=PENDING`
- 重新注册 APScheduler 任务

## 3. 提醒投递规则（完整）

### 3.1 时间精度

- 相对短时间（`X秒后`、`X分钟后`）按秒级触发
- 较大时间（`X小时后`、绝对时间如`明天12点`）按分钟级触发

### 3.2 内容解析

- 先用 LLM 提取 `reminder_content`
- 再做清洗与占位词过滤（避免 `提醒：我`）
- 提取失败时兜底标题：`待办提醒`

### 3.3 多端广播

到点后统一 fanout 到该用户所有绑定身份：
- `telegram / feishu / wechat / qq`：文本消息
- `web`：WS 通知（右上角悬浮卡片 + 音效由前端处理）
- `miniapp`：
  - 在线：WS 通知
  - 离线：微信订阅消息（`subscribe/send`）

## 4. 小程序配置

在 `.env` 配置：

```env
MINIAPP_APP_ID=
MINIAPP_APP_SECRET=
MINIAPP_SUBSCRIBE_TEMPLATE_ID=
MINIAPP_PAGE_PATH=pages/chat/index
MINIAPP_LANG=zh_CN
MINIAPP_SUBSCRIBE_CONTENT_KEY=thing1
MINIAPP_SUBSCRIBE_TIME_KEY=time2
```

说明：
- 订阅模板字段不同项目不一致，`*_KEY` 可改成你模板里的真实字段名。
- 若未配置 `MINIAPP_SUBSCRIBE_TEMPLATE_ID`，小程序离线订阅投递会失败并记录 `FAILED`，但在线 WS 仍可收到。

## 5. 前端/小程序端实现建议（完整）

- 登录页：
  - `wx.login()` 获取 `code`
  - 调 `POST /api/miniapp/auth/login`
  - 本地保存 `JWT`
- 聊天页：
  - 调 `POST /api/chat/send`，传 `source_platform=miniapp`
  - 建立 `ws://.../api/notifications/ws?token=JWT`
- 提醒：
  - 设置提醒前请求 `wx.requestSubscribeMessage`
  - 用户同意后可接收离线提醒
- 同步：
  - 首屏拉 `/api/chat/history`、`/api/calendar`、`/api/stats/ledger`
  - 收到 WS 的 `reminder/message` 后刷新相关查询

## 6. 生产上线清单

- 强制 HTTPS
- 配置稳定域名白名单（小程序 request/socket 合法域名）
- JWT_SECRET 至少 32 位随机串
- Postgres/Redis 启用持久化与备份
- 监控以下事件：
  - reminder_dispatched
  - reminder_deliveries FAILED 比例
  - 平台 API 错误码分布

