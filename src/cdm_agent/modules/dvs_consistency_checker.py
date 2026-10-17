import json
import os
import re
from dataclasses import dataclass

import pandas as pd
import xlsxwriter
from mcp.types import SamplingMessage, TextContent

from cdm_agent.modules.excel_theme import create_formats

BATCH_SIZE = 20  # Number of entries per LLM sampling request
CONSISTENCY_SHEET_NAME = "Consistency Check"

COLUMN_DVS_ID = "DVS ID"
COLUMN_DVS_TYPE = "DVS Type"
COLUMN_DESCRIPTION = "Description"


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class ConsistencyResult:
    dvs_id: str
    dvs_type: str
    description: str
    specification: str
    is_consistent: bool | None  # None = skipped (no spec or no description)
    confidence: str             # "high", "medium", "low", "na"
    issue: str | None


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _build_batch_prompt(entries: list[dict]) -> str:
    """Builds the LLM prompt for a batch of DVS entries."""
    entries_json = json.dumps(entries, ensure_ascii=False, indent=2)
    return f"""You are a Clinical Data Management (CDM) expert reviewing DVS (Data Validation Specifications).

For each entry, determine whether the natural language Description is logically consistent with the formal Specification.

Specification grammar reference:
- CHECK(...): a system query triggered when the condition is true
- ACTIVATE(WHEN=...): activates an item when the condition is true
- DEACTIVATE(WHEN=...): deactivates an item when the condition is true
- ASSIGN(VALUE=..., WHEN=...): assigns a value when the condition is true

Return ONLY a valid JSON array with no surrounding text or markdown:
[
  {{
    "dvs_id": "<dvs_id>",
    "is_consistent": <true or false>,
    "confidence": "<high, medium, or low>",
    "issue": "<one-sentence explanation if inconsistent, null if consistent>"
  }}
]

Entries to review:
{entries_json}"""


def _parse_llm_response(
    response_text: str,
    entries: list[dict],
) -> list[dict]:
    """
    Parses the LLM response and returns a list of result dicts keyed by dvs_id.
    Falls back to marking all entries as unverified if parsing fails.
    """
    json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
    if not json_match:
        return []

    try:
        parsed = json.loads(json_match.group(0))
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _read_dvs_entries(
    input_file: str,
    sheet_name: str,
    header_row: int,
    column_dvs_id: str,
    column_dvs_type: str,
    column_description: str,
    column_specification: str,
) -> list[dict]:
    """Reads DVS entries from the Excel file for consistency checking."""
    with pd.ExcelFile(input_file) as excel_file:
        if sheet_name not in excel_file.sheet_names:
            raise ValueError(
                f"Sheet '{sheet_name}' not found. "
                f"Available sheets: {excel_file.sheet_names}"
            )
        dataframe = excel_file.parse(sheet_name, header=header_row)

    required_columns = [column_dvs_id, column_dvs_type, column_description, column_specification]
    missing_columns = [col for col in required_columns if col not in dataframe.columns]
    if missing_columns:
        raise ValueError(
            f"Required columns not found: {missing_columns}. "
            f"Available columns: {list(dataframe.columns)}"
        )

    entries = []
    for _, row in dataframe.iterrows():
        dvs_id = str(row.get(column_dvs_id, "")).strip()
        if not dvs_id or dvs_id == "nan":
            continue
        entries.append({
            "dvs_id": dvs_id,
            "dvs_type": str(row.get(column_dvs_type, "")).strip(),
            "description": str(row.get(column_description, "")).strip(),
            "specification": str(row.get(column_specification, "")).strip(),
        })
    return entries


def _build_results(
    entries: list[dict],
    llm_results_by_id: dict[str, dict],
) -> list[ConsistencyResult]:
    """Merges LLM results with entry data into ConsistencyResult objects."""
    results = []
    for entry in entries:
        dvs_id = entry["dvs_id"]
        description = entry["description"]
        specification = entry["specification"]
        is_empty_description = not description or description == "nan"
        is_empty_specification = not specification or specification in ("nan", "NA")

        if is_empty_description or is_empty_specification:
            results.append(ConsistencyResult(
                dvs_id=dvs_id,
                dvs_type=entry["dvs_type"],
                description=description,
                specification=specification,
                is_consistent=None,
                confidence="na",
                issue=None,
            ))
            continue

        llm_result = llm_results_by_id.get(dvs_id)
        if llm_result is None:
            results.append(ConsistencyResult(
                dvs_id=dvs_id,
                dvs_type=entry["dvs_type"],
                description=description,
                specification=specification,
                is_consistent=None,
                confidence="na",
                issue="LLM response not received for this entry.",
            ))
        else:
            results.append(ConsistencyResult(
                dvs_id=dvs_id,
                dvs_type=entry["dvs_type"],
                description=description,
                specification=specification,
                is_consistent=bool(llm_result.get("is_consistent", True)),
                confidence=str(llm_result.get("confidence", "low")),
                issue=llm_result.get("issue"),
            ))
    return results


# ---------------------------------------------------------------------------
# Excel Writer
# ---------------------------------------------------------------------------

def write_consistency_sheet(
    workbook: xlsxwriter.Workbook,
    results: list[ConsistencyResult],
) -> None:
    """Writes the consistency check results to a new sheet in the given workbook."""
    worksheet = workbook.add_worksheet(CONSISTENCY_SHEET_NAME)
    formats = create_formats(workbook)

    headers = ["DVS ID", "DVS Type", "Status", "Confidence", "Description", "Specification", "Issue"]
    column_widths = [20, 22, 14, 12, 55, 55, 55]

    for col_index, (header_name, column_width) in enumerate(zip(headers, column_widths)):
        worksheet.set_column(col_index, col_index, column_width)
        worksheet.write(0, col_index, header_name, formats["header"])

    worksheet.freeze_panes(1, 0)

    for row_index, result in enumerate(results, start=1):
        if result.is_consistent is None:
            status_text = "NA"
            status_format = formats["skipped"]
        elif result.is_consistent:
            status_text = "CONSISTENT"
            status_format = formats["valid"]
        else:
            status_text = "INCONSISTENT"
            status_format = formats["invalid"]

        confidence_format = (
            formats["confidence_low"] if result.confidence == "low"
            else formats["normal"]
        )

        worksheet.write(row_index, 0, result.dvs_id, formats["normal"])
        worksheet.write(row_index, 1, result.dvs_type, formats["normal"])
        worksheet.write(row_index, 2, status_text, status_format)
        worksheet.write(row_index, 3, result.confidence.upper(), confidence_format)
        worksheet.write(row_index, 4, result.description, formats["normal"])
        worksheet.write(row_index, 5, result.specification, formats["normal"])
        worksheet.write(row_index, 6, result.issue or "", formats["issue"] if result.issue else formats["normal"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def run_consistency_check(
    input_file: str,
    ctx,
    output_file: str | None = None,
    sheet_name: str = "Data Validation Specifications",
    header_row: int = 1,
    column_dvs_id: str = COLUMN_DVS_ID,
    column_dvs_type: str = COLUMN_DVS_TYPE,
    column_description: str = COLUMN_DESCRIPTION,
    column_specification: str = "Specification",
) -> str:
    """
    Reads DVS entries, sends batches to the connected LLM client via MCP sampling,
    and writes a consistency check report Excel file.
    """
    if not output_file:
        base_name = os.path.splitext(input_file)[0]
        output_file = f"{base_name}_Consistency_Check.xlsx"

    entries = _read_dvs_entries(
        input_file, sheet_name, header_row,
        column_dvs_id, column_dvs_type, column_description, column_specification,
    )

    # Filter entries that have both Description and Specification to check
    checkable_entries = [
        entry for entry in entries
        if entry["description"] not in ("", "nan")
        and entry["specification"] not in ("", "nan", "NA")
    ]

    # Process in batches via MCP sampling
    llm_results_by_id: dict[str, dict] = {}
    total_batches = (len(checkable_entries) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_index in range(total_batches):
        batch = checkable_entries[batch_index * BATCH_SIZE:(batch_index + 1) * BATCH_SIZE]
        prompt = _build_batch_prompt(batch)

        response = await ctx.session.create_message(
            messages=[
                SamplingMessage(
                    role="user",
                    content=TextContent(type="text", text=prompt),
                )
            ],
            max_tokens=4096,
        )
        response_text = response.content.text if hasattr(response.content, "text") else str(response.content)

        batch_results = _parse_llm_response(response_text, batch)
        for result in batch_results:
            if isinstance(result, dict) and "dvs_id" in result:
                llm_results_by_id[result["dvs_id"]] = result

    results = _build_results(entries, llm_results_by_id)

    workbook = xlsxwriter.Workbook(output_file)
    try:
        write_consistency_sheet(workbook, results)
    finally:
        workbook.close()

    consistent_count = sum(1 for result in results if result.is_consistent is True)
    inconsistent_count = sum(1 for result in results if result.is_consistent is False)
    skipped_count = sum(1 for result in results if result.is_consistent is None)

    return (
        f"Consistency check complete: {len(entries)} entries processed "
        f"({consistent_count} consistent, {inconsistent_count} inconsistent, {skipped_count} skipped). "
        f"Report saved to: {os.path.abspath(output_file)}"
    )
