# PAI Mobile

独立的 React Native / Expo 客户端，用来复用当前项目的后端 API，面向安卓和 iOS。

## 当前已接入

- 邮箱密码登录
- 邮箱验证码登录
- 邮箱验证码注册
- 邮箱验证码重置密码
- SSE 流式逐字输出
- 用户资料读取
- 会话列表读取 / 切换 / 新建
- 聊天历史读取
- 普通消息发送
- 前台 WebSocket 提醒通知

## 当前未覆盖

- 图片上传与拍照
- 原生推送（APNs / FCM）
- 后端 `source_platform=app` 独立来源标识

## 启动

1. 安装依赖

```bash
cd mobile
npm install
```

2. 配置接口地址

复制 `.env.example` 为 `.env`，把地址改成移动设备能访问到的后端地址：

```bash
EXPO_PUBLIC_API_BASE_URL=http://192.168.1.10:8000
```

3. 启动 Expo

```bash
npm run start
```

## 说明

- 如果你用的是真机，不要写 `localhost`，要写电脑的局域网 IP。
- 目前移动端发送消息时仍以 `web` 来源接入后端，因为后端暂时只接受 `web` / `miniapp`。
- 如果要做真正的移动端产品化，下一步应该先补后端 `app` 平台标识和 APNs / FCM 推送链路。
