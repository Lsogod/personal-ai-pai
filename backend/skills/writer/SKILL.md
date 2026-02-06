---
name: writer
description: Draft, polish, summarize, and rewrite Chinese or bilingual text with clear structure and practical tone.
---

# Trigger
Use this skill when the user asks for writing, polishing, rewriting, summarizing, title generation, or style transformation.

# Workflow
1. Identify output goal and target reader from user input.
2. If key constraints are missing, choose practical defaults and proceed.
3. Produce a structured draft first, then refine wording for clarity.
4. Keep paragraphs compact and avoid filler language.
5. If domain constraints are needed, load relevant references before finalizing.

# Constraints
- Preserve user-provided facts, names, and numbers.
- Do not fabricate citations or data.
- Keep tone aligned with user intent; default to professional and concise.
- When uncertainty affects correctness, ask one focused clarification question.

# Output Contract
Return Markdown with this shape:
- `Result:` final text
- `Optional Notes:` only when assumptions or ambiguities matter
