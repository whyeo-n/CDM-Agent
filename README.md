# CDM Agent

A unified MCP (Model Context Protocol) server for automating Clinical Data Manager (CDM) workflows. Provides Excel comparison, DVS validation, Blank eCRF PDF comparison, DB Spec validation, SOP log verification, and more through any MCP-compatible AI agent. Published on PyPI as [`cdm-agent`](https://pypi.org/project/cdm-agent/).

---

## Available Tools (13)

### Excel & DB Spec

- **read_excel_info**: Reads an Excel file and returns its structure — sheet names, column names, row counts, and a preview of the first few rows. Use this first when sheet names or column names are unknown.
- **compare_common_excel**: Compares specific sheets of two Excel files and generates a difference report. Requires specifying the sheet name, header row, and primary key column.
- **compare_db_spec**: Compares full CDM DB Spec workbooks. Automatically detects header positions and updates the Revision History sheet.

### DVS (Data Validation Specifications)

- **download_dvs_grammar**: Copies the bundled DVS Specification Grammar reference file (`DVS_SPEC_GRAMMAR.md`) to a specified directory. Use this when a user needs the grammar definition for writing or reviewing DVS Specifications column entries.
- **validate_dvs_specifications**: Reads a DVS Excel file, validates all Specification entries against the DVS grammar, and generates a detailed validation report. Reports syntax errors and auto-corrected suggestions where possible.
- **check_dvs_consistency** _(requires MCP Sampling)_: Checks whether the natural language Description is logically consistent with the formal Specification for each DVS entry. Results are written to a new Excel report.

### Google Calendar Integration

- **download_timeline_template**: Copies the bundled DM Timeline Excel template to a specified directory. Use this before filling in timeline data and calling `make_google_calendar_csv`.
- **make_google_calendar_csv**: Extracts key schedules from a Timeline Excel file and converts them into a CSV formatted for Google Calendar import.

### Blank eCRF PDF Comparison

- **compare_ecrf_pdf_single**: Compares two Blank eCRF PDF files and produces a single side-by-side comparison PDF. Changed pages are shown with Before (left) and After (right) layouts. Annotation colors: red = deleted, green = added, yellow = modified.

### DB Spec Validation

- **validate_db_spec**: Validates a DB Specification Excel file against CDASH standards (CDASHIG v2.3 / SDTMIG v3.4). Runs three checks: domain validity, ITEM ID suffix vs. DB TYPE consistency, and CODE field duplicate code numbers. Generates an Excel validation report with per-check result sheets and a summary.
- **generate_cdash_variables**: Generates or updates the CDASH variables JSON reference from a CDASH Model Excel file. Use this when a new version of the CDASH Model is released. Supports merge mode to add new entries without replacing existing data.

### SOP Documents

- **list_dm_documents**: Reads Appendix entries from an SOP Log Excel file and lists the matching signed PDF documents found recursively in a target folder. The Excel report flags documents found only in Working folders and missing signed documents.
- **verify_sop_log**: Compares the SOP Log Excel against the SOP Index PDF and reports version and effective date discrepancies for both SOP and Appendix entries.

---

## Installation

> **Windows users:** This server must be run inside **WSL (Windows Subsystem for Linux)**. All commands below should be run in a WSL terminal, not PowerShell or CMD. If WSL is not installed, follow [Microsoft's WSL setup guide](https://learn.microsoft.com/en-us/windows/wsl/install) first.
>
> After installing WSL, open a WSL terminal and execute below commands.

### Step 1. Install uv

`uv` is a fast Python package manager. Installing it also gives you `uvx`, which runs packages without a separate Python installation.

**Mac / Linux / WSL**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 2. Set Up Your AI Agent

Follow the section for your AI agent. Each section covers registration, skill installation, and connection verification.

---

#### Claude Code

**Register the MCP server**

```bash
claude mcp add --scope user cdm-agent uvx cdm-agent@latest
```

**Install skills** (optional — document review, translation, revision history)

```bash
uvx --from cdm-agent cdm-install-skills --tool claude
```

Skills are installed into `.claude/skills/` in your project directory.

**Verify the connection**

```bash
claude mcp list
```

---

#### Gemini CLI

**Prerequisite — Node.js**

Gemini CLI requires Node.js. Install it via nvm if you don't have it.

*Mac / Linux*

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
nvm install --lts
```

*Windows* — Download and install [nvm-windows](https://github.com/coreybutler/nvm-windows/releases), then:

```powershell
nvm install lts
nvm use lts
```

**Install Gemini CLI and register the MCP server**

```bash
npm install -g @google/gemini-cli
gemini mcp add cdm-agent uvx --args cdm-agent@latest --scope user
```

**Install skills** (optional — document review, translation, revision history)

```bash
uvx --from cdm-agent cdm-install-skills --tool gemini
```

Skills are installed into `.gemini/skills/` in your project directory.

**Verify the connection**

```bash
gemini mcp list
```

---

#### Codex CLI

**Register the MCP server**

```bash
codex mcp add cdm-agent -- uvx cdm-agent@latest
```

**Install skills** (optional — document review, translation, revision history)

```bash
uvx --from cdm-agent cdm-install-skills --tool codex
```

Skills are installed into `.agents/skills/` in your project directory.

**Verify the connection**

```bash
codex mcp list
```

---

You should see `cdm-agent` listed as connected.

> **Set up skills and agent config files together**
>
> `cdm-init` installs the bundled skills and the CDM workflow rules (`.claude/rules/cdm-domain.md`, `GEMINI.md`, `AGENTS.md`) into your project directory. Existing files are never overwritten.
>
> ```bash
> uvx --from cdm-agent cdm-init --tool claude   # or gemini / codex / --all
> ```

> **Install skills for all agents at once**
>
> If you use multiple AI tools in the same project directory, run:
>
> ```bash
> uvx --from cdm-agent cdm-install-skills --all
> ```

---

## Usage

Once connected, simply describe what you need in natural language:

> "Compare the previous and current DB Spec files and generate a difference report."

> "Validate the DVS file and report any syntax errors."

> "Download the Timeline template, then convert the completed file into a Google Calendar CSV."

The AI agent will automatically select and call the appropriate tool.

---

## Troubleshooting

### MCP server shows as disconnected

The most common cause is that `uvx` is not in the PATH when the AI tool launches the MCP server process.

**1. Find the `uvx` location**

```bash
which uvx
```

**2. Add its directory to PATH**

Replace `~/.local/bin` with the directory from the output above:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

For zsh:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

Then restart the AI tool and verify the connection again.

---

## Project Structure

```
src/
└── cdm_agent/
    ├── __init__.py
    ├── server.py                   # MCP server entry point (13 tools)
    ├── install_skills.py           # cdm-install-skills CLI
    ├── init_agent.py               # cdm-init CLI (skills + agent config files)
    ├── agent_configs/              # Workflow rules for Claude Code / Gemini CLI / Codex CLI
    ├── skills/                     # cdm-review, cdm-translate, cdm-revision
    ├── data/
    │   └── cdash_variables.json    # CDASH variables reference
    ├── templates/
    │   ├── DVS_SPEC_GRAMMAR.md     # DVS Specification grammar reference
    │   └── Timeline for Clinical Data Manager_2026Q1.xlsx
    └── modules/
        ├── excel_theme.py          # Shared Excel formatting standards
        ├── excel_compare.py        # Excel & DB Spec comparison engine
        ├── excel_info.py           # Excel structure inspector
        ├── excel_inspector.py      # Excel metadata reader
        ├── calendar_maker.py       # Timeline parsing & Calendar CSV generator
        ├── dvs_parser.py           # DVS specification grammar parser
        ├── dvs_excel_writer.py     # DVS validation report writer
        ├── dvs_consistency_checker.py  # LLM-powered DVS consistency checker (MCP Sampling)
        ├── db_spec_validator.py    # CDASH-based DB Spec validator
        ├── cdash_reference_generator.py  # CDASH variables JSON generator
        ├── pdf_ecrf_compare.py     # Blank eCRF PDF comparison engine
        ├── sop_document_lister.py  # SOP Appendix signed-document lister
        ├── sop_log_verifier.py     # SOP Log vs SOP Index PDF verifier
        └── lab_normal_range_converter.py  # Lab Normal Range Excel to JSON converter
```
