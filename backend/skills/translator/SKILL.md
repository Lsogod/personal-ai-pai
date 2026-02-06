---
name: translator
description: Translate text between Chinese and other languages with terminology consistency and natural phrasing.
---

# Trigger
Use this skill when the user requests translation, bilingual conversion, localization rewrite, or terminology-preserving translation.

# Workflow
1. Detect source language and infer target language from request.
2. If target language is omitted, default target to Chinese.
3. Translate meaning first, then adjust wording to natural target-language expression.
4. Preserve names, product terms, numbers, dates, and formatting markers.
5. For domain terms, consult `references/terminology.md` when available.

# Constraints
- Do not add facts not present in source text.
- Keep technical terms consistent within one output.
- Keep output concise unless user asks for detailed explanation.

# Output Contract
Return Markdown:
- `Translation:` translated text
- `Optional Notes:` only when ambiguity or multiple valid interpretations exist
