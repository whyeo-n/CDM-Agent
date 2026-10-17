# Project Instructions

- **Framework:** Python + uv package manager
- **Purpose:** CDM Agent — MCP server for Clinical Data Managers. Data integrity cannot be compromised.
- **Strict Change Control:** If an error occurs, do NOT modify tool code independently. Report the error and its context to the user.
- **Linting:** Use `ruff check src/` for linting after any Python file change.

# Development Workflows

## Adding a New Tool

1. Implement logic in `src/cdm_agent/modules/<module_name>.py`
2. Register in `src/cdm_agent/server.py` with `@mcp.tool()` decorator
3. Add wheel include pattern in `pyproject.toml` if new data files are added
4. Run `cdm-update-docs` skill to update README.md

## Testing Locally

```bash
uv run --directory <project-dir> cdm-agent   # dev MCP server (registered as cdm-local)
uv run ruff check src/                                 # lint
```

After editing `server.py` or any module, run `/mcp` in Claude Code to reconnect `cdm-local`.

To re-register the dev server:
```bash
claude mcp remove cdm-local
claude mcp add --scope user cdm-local "uv run --directory <project-dir> cdm-agent"
```

## Publishing

```bash
# 1. Bump version in pyproject.toml
# 2. Commit and tag
git tag v<version>
# 3. Build and publish
uv build && uv publish
```

# Skills

## Registered Skills (developer)

- Documentation update request after adding a new tool or skill → `cdm-update-docs`

## Rule

Whenever a new **developer** skill is created, add its trigger condition here:
`- trigger condition → skill-name`
