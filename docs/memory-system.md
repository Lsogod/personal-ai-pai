# 长期记忆系统

> 本文档描述 PAI 长期记忆系统的设计与实现。随版本迭代持续更新。
>
> 最后更新：2026-03-31 · 分支：`main`

---

## 概览

系统为每位用户维护独立的长期记忆，跨会话持久化，无需用户手动操作。当前实现采用 `PostgreSQL` 保存长期记忆真值，`Milvus` 保存向量索引；记忆在对话中自动提取，也可由 Agent 显式写入，查询时按相关性从 Milvus 召回，再回 PostgreSQL 取真值内容。

```
用户消息 ──┬──→ LangGraph 节点处理 ──→ 回复
           │         ↑
           │    相关记忆注入（Milvus 召回 top-k）
           │
           ├──→ 异步提取管道 ──→ PostgreSQL
           ├──→ Agent 工具调用 ──→ PostgreSQL
           └──→ memory_index_worker ──→ Embedding ──→ Milvus
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
├── vector_status   (DIRTY / SYNCED / FAILED)
├── vector_synced_at
├── vector_error
├── vector_model
├── vector_version
├── vector_text_hash
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

### 向量同步

长期记忆先落 PostgreSQL，再标记 `vector_status=DIRTY`。独立 `memory_index_worker` 会定时扫描 `DIRTY / FAILED` 记录：

1. 构造索引文本：`[{memory_type}] {memory_key}: {content}`
2. 调用 embedding 模型生成向量
3. `upsert` 到 Milvus collection
4. 成功后把 PostgreSQL 中对应行标记为 `SYNCED`

### memory_worker 补扫

独立后台进程，定时扫描 `memory_status` 为 PENDING 或 FAILED 的消息，重新执行提取管道，确保最终一致性。

---

## 4. 读取：Milvus 召回 + PostgreSQL 回表

每轮对话时，系统根据 `LONG_TERM_MEMORY_RETRIEVE_MODE` 决定读取方式：

- `full_inject`：直接注入当前用户全部有效长期记忆
- `dense`：优先调用 `retrieve_relevant_long_term_memories(...)`

当模式为 `dense` 时，读取流程如下：

```text
① 将 query = 用户当前问题 + 会话摘要
② 用 embedding 模型生成 query vector
③ 去 Milvus 搜索 top-N memory_id
④ 回 PostgreSQL 取这些 memory_id 的真值内容
⑤ 应用层按 retrieval_score / importance / confidence / recency / exact_key_bonus 重排
⑥ 返回 top-k 注入 prompt
```

这意味着：

- `PostgreSQL` 决定“这条记忆真实是什么”
- `Milvus` 决定“当前最该召回哪几条记忆”

如果向量检索失败，系统会自动回退到词法扫描，不会直接中断主对话链路。

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
| `MEMORY_INDEX_WORKER_ENABLED` | `false` | 向量索引同步 worker 开关 |
| `MEMORY_INDEX_WORKER_INTERVAL_SEC` | `30` | 向量同步轮询间隔 |
| `MEMORY_INDEX_WORKER_BATCH_SIZE` | `32` | 每轮同步批量大小 |
| `MEMORY_EMBEDDING_MODEL` | `text-embedding-3-small` | 长期记忆 embedding 模型 |
| `MEMORY_EMBEDDING_DIM` | `1536` | 向量维度 |
| `MEMORY_MILVUS_ENABLED` | `false` | 是否启用 Milvus 检索 |
| `MEMORY_MILVUS_URI` | - | Milvus 连接地址 |
| `MEMORY_MILVUS_COLLECTION` | `memory_text_v1` | 记忆向量 collection |

---

## 8. 关键文件

| 文件 | 职责 |
|------|------|
| `backend/app/models/memory.py` | 数据模型定义 |
| `backend/app/services/memory.py` | 核心逻辑：提取、精炼、写入、检索、去重、清洗 |
| `backend/app/services/memory_embeddings.py` | 记忆 embedding 封装 |
| `backend/app/services/memory_vector_store.py` | Milvus collection / upsert / search |
| `backend/app/services/message_handler.py` | 记忆注入 + 异步提取调度 |
| `backend/app/services/tool_executor.py` | memory_save/append/delete/list 工具实现 |
| `backend/app/services/toolsets.py` | 节点工具权限注册 |
| `backend/app/memory_worker.py` | 后台补扫进程 |
| `backend/app/memory_index_worker.py` | 后台同步向量索引 |
| `backend/app/core/config.py` | 配置参数 |

---

## 9. 版本记录

| 日期 | 变更 |
|------|------|
| 2026-03-31 | 接入 PostgreSQL 真值 + Milvus 检索 + memory_index_worker，同步补充“短期规则不提取”为长期记忆的边界说明 |
| 2026-03-28 | 全量注入改为相关性检索（top-20）；min_confidence 0.75→0.5；TTL 180→730 天；debounce 12s→0；上下文窗口 8000→24000；schedule/ledger 节点新增记忆工具 |
