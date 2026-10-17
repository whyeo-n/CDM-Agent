---
name: cdm-update-docs
description: >
  Scans current tools and skills in the project and updates README.md and CLAUDE.md
  to reflect the current state. Invoke manually after adding a new tool or skill.
user-invocable: true
---

You are maintaining documentation for a Clinical Data Management MCP server project.

Perform the following steps in order:

## Step 1 — Collect current tools

Read `src/cdm-agent/server.py` and extract all functions decorated with `@mcp.tool()`.
For each tool, collect:
- Function name
- Docstring (first line = short description)
- Parameters (names and types)

## Step 2 — Collect current skills

Read each `SKILL.md` file under `.claude/skills/*/SKILL.md`.
For each skill, collect:
- `name` field from frontmatter
- `description` field from frontmatter (first sentence only)

## Step 3 — Update README.md

Read `README.md` and update the **Available Tools** section only.

Rules:
- Keep the existing section groupings and heading structure
- Add any tool that exists in `server.py` but is missing from README.md
- Remove any tool listed in README.md that no longer exists in `server.py`
- Update descriptions to match current docstrings if they differ
- Do NOT modify any other section of README.md

## Step 4 — Update CLAUDE.md Registered Skills

Read `CLAUDE.md` and update the **Registered Skills** list under `## Registered Skills`.

Rules:
- Each line format: `- <trigger condition in Korean> → \`<skill-name>\``
- Add any skill that exists in `.claude/skills/` but is missing from CLAUDE.md
- Remove any skill listed in CLAUDE.md whose SKILL.md no longer exists
- Keep existing trigger condition wording unless the skill description has materially changed
- Do NOT modify any other section of CLAUDE.md

## Step 5 — Report

After all edits, output a brief summary:
- Tools added / removed / updated in README.md
- Skills added / removed / updated in CLAUDE.md
