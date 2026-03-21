# 微信小程序客户端接入指南

## 1. 已提供的客户端代码

项目目录：`miniapp/`

包含页面：
- `pages/login`：`wx.login` + 后端换取 JWT
- `pages/home`：首页入口，快捷功能卡片
- `pages/chat`：聊天、多图上传（base64 data URL）、实时通知 WS、工具步骤可视化、提醒悬浮卡片
- `pages/ledger`：账单列表与筛选统计
- `pages/calendar`：月视图账单/日程
- `pages/me`：个人中心与账号设置
  - `pages/me/binding`：跨平台身份绑定管理
  - `pages/me/skills`：技能管理
  - `pages/me/feedback`：问题反馈

## 2. 后端要求

请确保后端是最新版本，并启用了以下接口：
- `POST /api/miniapp/auth/login`
- `POST /api/chat/send`
- `GET /api/chat/history`
- `GET /api/calendar`
- `GET /api/stats/ledger`
- `GET /api/user/profile`
- `GET /api/user/identities`
- `POST /api/user/bind-code`
- `POST /api/user/bind-consume`
- `WS /api/notifications/ws?token=...`

## 3. 配置

### 3.1 后端 `.env`

```env
MINIAPP_APP_ID=你的小程序AppID
MINIAPP_APP_SECRET=你的小程序AppSecret
MINIAPP_SUBSCRIBE_TEMPLATE_ID=你的提醒模板ID
MINIAPP_PAGE_PATH=pages/chat/index
MINIAPP_LANG=zh_CN
MINIAPP_SUBSCRIBE_CONTENT_KEY=thing1
MINIAPP_SUBSCRIBE_TIME_KEY=time2
```

### 3.2 小程序前端配置

编辑：`miniapp/config.js`

```js
module.exports = {
  API_BASE_URL: "https://你的后端域名",
  SUBSCRIBE_TEMPLATE_ID: "你的提醒模板ID"
};
```

## 4. 微信开发者工具导入

1. 打开微信开发者工具。
2. 导入项目目录：`miniapp/`。
3. 把 `project.config.json` 里的 `appid` 改为你自己的小程序 AppID。
4. 在小程序后台配置合法域名：
- `request` 合法域名：后端 HTTPS 域名
- `socket` 合法域名：后端 WSS 域名
5. 预览/真机调试。

## 5. 提醒实现说明

提醒到点后，后端会：
- 广播到所有已绑定身份（Telegram/飞书/微信/QQ/Web/Miniapp）
- 写入 `reminder_deliveries` 投递记录
- Miniapp 在线时通过 WS 实时推送
- Miniapp 离线时尝试模板订阅消息推送

## 6. 常见问题

### 6.1 小程序发不出请求
- 检查 `API_BASE_URL` 是否 HTTPS
- 检查小程序后台是否加了合法域名

### 6.2 收不到离线提醒
- 检查用户是否在 `我的` 页面点过“请求订阅授权”
- 检查 `MINIAPP_SUBSCRIBE_TEMPLATE_ID` 与字段 key 是否正确
- 查看后端 `reminder_deliveries` 失败原因

### 6.3 图片识别失败
- 小程序已把图片转 `data:image/...;base64` 上传
- 若仍失败，优先检查模型配置与 token
