# 长期记忆系统

> 本文档描述 PAI 长期记忆系统的设计与实现。随版本迭代持续更新。
>
> 最后更新：2026-03-31 · 分支：`main`

---

## 概览

系统为每位用户维护独立的长期记忆，跨会话持久化，无需用户手动操作。当前实现以 `PostgreSQL` 作为长期记忆真值库；记忆在对话中自动提取，也可由 Agent 显式写入，查询时按相关性评分注入 prompt。

```
用户消息 ──┬──→ LangGraph 节点处理 ──→ 回复
           │         ↑
           │    相关记忆注入（top-k）
           │
           └──→ 异步提取管道 ──→ DB
                  Agent 工具调用 ──→ DB
```

---

## 1. 记忆类型

| 类型 | 用途 | 示例 key → content |
|------|------|--------------------|
| `preference` | 偏好习惯 | `preference.cuisine` → 川菜 |
| `fact` | 用户事实 | `fact.residence_city` → 武汉 |
| `goal` | 目标计划 | `goal.current_exam` → CPA 考试 |
| `project` | 项目上下文 | `project.app_refactor` → 正在重构前端 |
| `constraint` | 规则约束 | `constraint.reply_language` → 中文 |

> `profile` 类型（昵称、AI 名称、emoji 等）**不存入记忆表**，由 User 模型独立管理，防止记忆表与用户档案状态不一致。

---

## 2. 数据模型

```
long_term_memories
├── id              (PK)
├── user_id         (FK → users, indexed)
├── conversation_id (FK → conversations)
├── source_message_id (FK → messages)
├── memory_key      (max 160, indexed, unique per user)
├── memory_type     (max 40, indexed)
├── content         (max 1000)
├── importance      (1-5, default 3, indexed)
├── confidence      (0-1, default 0.8)
├── is_active       (bool)
├── last_accessed_at (nullable, 检索时更新)
├── expires_at      (nullable, TTL)
├── created_at
└── updated_at

UNIQUE(user_id, memory_key)
```

每条用户消息追踪处理状态：

```
messages
├── memory_status       (PENDING → PROCESSED / FAILED / SKIPPED)
├── memory_processed_at
└── memory_error
```

---

## 3. 写入：双通道机制

### 通道 1 — Agent 显式调用（实时）

Agent 在对话中判断出现值得记住的信息时，直接调用工具：

| 工具 | 作用 |
|------|------|
| `memory_save` | 创建或覆盖一条记忆 |
| `memory_append` | 追加内容到已有记忆 |
| `memory_delete` | 删除指定记忆 |
| `memory_list` | 列出用户所有记忆 |

写入成功后立即标记消息为 `PROCESSED`，跳过异步管道，避免重复提取。

**覆盖节点**：chat_manager / schedule_manager / ledger_manager 均可调用。

### 通道 2 — 后台异步提取（兜底）

每轮对话结束后自动触发三阶段管道：

```
阶段 1: extract_memory_candidates（LLM 提取）
  输入：会话摘要 + 完整上下文（≤24000 字符）+ 用户消息 + 助手回复
  输出：候选记忆数组
  规则：
    ✓ 30 天后仍有价值
    ✓ 稳定偏好、长期事实、长期目标、长期项目、长期约束
    ✓ 用户显式"记住这个" → 提高 importance/confidence
    ✓ 用户显式"忘记这个" → op=delete
    ✓ 保持用户原始语言
    ✗ 短期状态、天气快照、日/周汇总、系统日志
    ✗ 提醒、待办、计划执行步骤、某天/某周/某月临时要求
    ✗ 今天/明天/后天/这周/本月/这次 这类短期时间窗
    ✗ 仅针对短期窗口的条件规则，例如“如果今天花费超过100，明天提醒少花”
    ✗ 身份档案（昵称/AI名/emoji）
    ✗ 助手回复中的内容（不作为事实来源）

阶段 2: _llm_refine_memory_candidates（LLM 精炼）
  输入：候选记忆 + 用户已有记忆（最近 160 条）
  决策：逐条判断 keep/merge/discard
  规则：
    - 语义等价 → 归并为规范表述
    - 与已有记忆重复 → 更新旧记忆（merge_target_id）
    - key 可复用 → 复用已有 key
    - 不满足 30 天价值标准 → 丢弃

阶段 3: upsert_long_term_memories（DB 写入）
  过滤层：
    ① identity 类型 → 丢弃
    ② 内容为空 → 丢弃
    ③ confidence < 0.5 → 丢弃
    ④ 每轮最多 6 条
  Key 生成：LLM 提供 → LLM 推断 → SHA1 哈希兜底
  写入：key 匹配 → 更新；语义匹配（≥0.82）→ 更新；否则新建
  写入后强去重：≥0.9 相似度的冗余副本删除
```

### memory_worker 补扫

独立后台进程，定时扫描 `memory_status` 为 PENDING 或 FAILED 的消息，重新执行提取管道，确保最终一致性。

---

## 4. 读取：相关性检索

每轮对话时，以用户消息为查询词进行记忆检索（`retrieve_relevant_long_term_memories`），再回到 PostgreSQL 取真值内容：

```
① DB 取候选池
   WHERE user_id = ? AND 未过期
   ORDER BY importance DESC, updated_at DESC
   LIMIT 80（scan_limit）

② 逐条评分
   score = token_overlap × 0.7 + importance × 0.2 + recency × 0.1

③ 筛选
   词汇重叠分 ≥ 0.12 的优先入选
   不足 top_k 时用剩余高分记忆补齐
   排除 identity 类记忆

④ 返回 top-20 注入 prompt
   更新 last_accessed_at
```

### Token 匹配算法

```
英文: \w{2,} 词组提取
中文: 2 字滑动窗口 bigram

"周末想吃火锅" → {"周末", "末想", "想吃", "吃火", "火锅"}

score = max(Jaccard, Containment × 0.92)
```

无需 embedding API，纯本地计算，支持中英文混合。

---

## 5. 生命周期管理

### TTL 过期

默认 730 天。每次检索/写入时检查 `expires_at`，超期记忆自动排除。

### 语义去重

| 阈值 | 行为 |
|------|------|
| ≥ 0.82 | 判定为语义重复，写入时复用已有记忆 |
| ≥ 0.90 | 强制合并，删除冗余副本 |

### 定期清洗（consolidate）

通过管理 API 触发，LLM 审查用户全部记忆：
- 逐条决策：保留 / 删除 / 合并到另一条
- 删除过时事实、重复项、短期状态残留
- 更新 importance/confidence/content

### 消息级追踪

每条用户消息独立记录处理状态：

```
PENDING → PROCESSED  （提取成功）
       → FAILED      （超时/异常，worker 可重试）
       → SKIPPED     （命令消息/空内容等）
```

---

## 6. 安全边界

- **identity 隔离**：`preferred_name`、`nickname`、`ai_name`、`ai_emoji`、`assistant_name` 以及 `memory_type=profile` 的内容不进入记忆表
- **用户隔离**：`UNIQUE(user_id, memory_key)` 约束 + 所有查询强制 `WHERE user_id = ?`
- **内容上限**：单条记忆最多 1000 字符
- **写入频率**：单轮最多 6 条，有 debounce 控制

---

## 7. 配置参数

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `LONG_TERM_MEMORY_ENABLED` | `true` | 总开关 |
| `LONG_TERM_MEMORY_MIN_CONFIDENCE` | `0.5` | 写入最低置信度 |
| `LONG_TERM_MEMORY_MAX_WRITE_ITEMS` | `6` | 单轮最多写入条数 |
| `LONG_TERM_MEMORY_RETRIEVE_LIMIT` | `20` | 检索注入 prompt 的条数上限 |
| `LONG_TERM_MEMORY_RETRIEVE_SCAN_LIMIT` | `80` | 检索候选扫描上限 |
| `LONG_TERM_MEMORY_DEFAULT_TTL_DAYS` | `730` | 默认过期天数 |
| `LONG_TERM_MEMORY_DEBOUNCE_SEC` | `0` | 异步提取延迟（秒） |
| `LONG_TERM_MEMORY_EXTRACT_TIMEOUT_SEC` | `90` | LLM 提取超时 |
| `LONG_TERM_MEMORY_UPSERT_TIMEOUT_SEC` | `90` | 写入超时 |
| `LONG_TERM_MEMORY_EXTRACT_CONTEXT_MAX_CHARS` | `24000` | 提取上下文窗口 |
| `LONG_TERM_MEMORY_SCAN_ENABLED` | `true` | memory_worker 开关 |
| `LONG_TERM_MEMORY_SCAN_INTERVAL_SEC` | `120` | worker 扫描间隔 |

---

## 8. 关键文件

| 文件 | 职责 |
|------|------|
| `backend/app/models/memory.py` | 数据模型定义 |
| `backend/app/services/memory.py` | 核心逻辑：提取、精炼、写入、检索、去重、清洗 |
| `backend/app/services/message_handler.py` | 记忆注入 + 异步提取调度 |
| `backend/app/services/tool_executor.py` | memory_save/append/delete/list 工具实现 |
| `backend/app/services/toolsets.py` | 节点工具权限注册 |
| `backend/app/memory_worker.py` | 后台补扫进程 |
| `backend/app/core/config.py` | 配置参数 |

---

## 9. 版本记录

| 日期 | 变更 |
|------|------|
| 2026-03-31 | 文档补充“短期规则不提取为长期记忆”的边界说明 |
| 2026-03-28 | 全量注入改为相关性检索（top-20）；min_confidence 0.75→0.5；TTL 180→730 天；debounce 12s→0；上下文窗口 8000→24000；schedule/ledger 节点新增记忆工具 |
