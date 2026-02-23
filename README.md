# PAI - Personal AI Assistant

Multi-platform personal assistant with a LangGraph-based backend, domain nodes, tool registry, and memory system.

## Architecture

![PAI System Architecture](docs/architecture.svg)

![PAI Agent Workflow](docs/agent-workflow.svg)

## What Changed (Latest)

- Updated graph routing and node orchestration documentation to match current code.
- Added explicit memory behavior in docs:
  - `long-term memory list query` now uses direct full-list retrieval.
  - normal chat still uses relevant-memory top-k retrieval for token efficiency.
  - long-term memory write remains asynchronous post-response.
- Updated diagrams to reflect current runtime:
  - `message_handler` command/pre-graph stage
  - `router_node` structured intent classification
  - `complex_task` eligibility routing
  - tool catalog propagation and node tool execution

## Current Backend Flow (Short)

1. Receive message in `message_handler`.
2. Run dedup/auth/quota/command checks.
3. Branch:
   - if user asks to list long-term memories: direct list retrieval + direct reply
   - else: preload relevant long-term memories and invoke graph
4. `router_node` classifies intent and routes to one node:
   - `onboarding`
   - `ledger_manager`
   - `schedule_manager`
   - `chat_manager`
   - `skill_manager`
   - `help_center`
   - `complex_task` (cross-node orchestration)
5. Send response.
6. Run async long-term memory extraction/upsert in background.

## Memory Model

- Storage table: `long_term_memories` (PostgreSQL).
- Read modes:
  - **List mode**: `list_long_term_memories(...)` for "show my long-term memories"-type requests.
  - **Relevant mode**: `retrieve_relevant_long_term_memories(...)` for normal chat context injection.
- Write mode:
  - background task after response:
    - `extract_memory_candidates(...)`
    - `upsert_long_term_memories(...)`
- Identity/profile-like entries are filtered out from long-term memory.

## Tooling Model

- Runtime tool metadata from `tool_registry` can be attached into state (`tool_catalog`).
- Nodes execute tools through unified wrappers (`toolsets` / `invoke_node_tool` and LangChain tool adapters).
- Supports common tools + node-scoped tools + MCP tools.

## Run with Docker

```bash
docker compose up -d --build
```

Services (default):

- Backend: `http://localhost:8000`
- Frontend: `http://localhost:3001`
- API docs: `http://localhost:8000/docs`

## Repo Layout (Key Paths)

- `backend/app/graph/workflow.py` - graph assembly
- `backend/app/graph/nodes/` - domain nodes
- `backend/app/services/message_handler.py` - request entry + orchestration
- `backend/app/services/memory.py` - long-term memory read/write logic
- `backend/app/services/tool_registry.py` - runtime tool metadata
- `backend/app/services/toolsets.py` - node tool execution wrappers
- `docs/architecture.svg` - system architecture diagram
- `docs/agent-workflow.svg` - lifecycle and routing diagram

## Notes

- The working tree may contain ongoing feature changes in other files. This README and the two SVG diagrams were updated to match the latest architecture behavior in code.
