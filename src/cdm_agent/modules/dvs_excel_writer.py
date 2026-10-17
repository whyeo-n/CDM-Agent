import os
from dataclasses import dataclass

import pandas as pd
import xlsxwriter

from cdm_agent.modules.dvs_parser import ParseResult, parse
from cdm_agent.modules.excel_theme import create_formats


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SHEET_NAME = "Data Validation Specifications"
DEFAULT_HEADER_ROW = 1  # 0-based index (row index 1 = second row in Excel)

COLUMN_DVS_ID = "DVS ID"
COLUMN_DVS_TYPE = "DVS Type"
COLUMN_SPECIFICATION = "Specification"

REPORT_SHEET_NAME = "DVS Validation Report"
SUMMARY_SHEET_NAME = "Summary"


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class DVSRow:
    row_index: int
    dvs_id: str
    dvs_type: str
    specification: str
    parse_result: ParseResult


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _read_dvs_dataframe(
    input_file: str,
    sheet_name: str,
    header_row: int,
    column_dvs_id: str,
    column_dvs_type: str,
    column_specification: str,
) -> pd.DataFrame:
    """Reads the DVS sheet from an Excel file and returns a DataFrame."""
    try:
        with pd.ExcelFile(input_file) as excel_file:
            available_sheets = excel_file.sheet_names
            if sheet_name not in available_sheets:
                raise ValueError(
                    f"Sheet '{sheet_name}' not found. "
                    f"Available sheets: {available_sheets}. "
                    f"Please re-call with the correct sheet_name parameter."
                )
            dataframe = excel_file.parse(sheet_name, header=header_row)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(
            f"Could not read '{os.path.basename(input_file)}': {error}"
        ) from error

    required_columns = [column_dvs_id, column_dvs_type, column_specification]
    missing_columns = [col for col in required_columns if col not in dataframe.columns]
    if missing_columns:
        raise ValueError(
            f"Required columns not found in sheet '{sheet_name}': {missing_columns}. "
            f"Available columns: {list(dataframe.columns)}. "
            f"Please re-call with the correct column name parameters "
            f"(column_dvs_id, column_dvs_type, column_specification)."
        )

    return dataframe


def _parse_all_rows(
    dataframe: pd.DataFrame,
    column_dvs_id: str,
    column_dvs_type: str,
    column_specification: str,
) -> list[DVSRow]:
    """Runs the parser on every row and returns a list of DVSRow results."""
    parsed_rows = []
    for row_index, row in dataframe.iterrows():
        dvs_id = str(row.get(column_dvs_id, "")).strip()
        dvs_type = str(row.get(column_dvs_type, "")).strip()
        specification = row.get(column_specification, "")

        if dvs_id in ("", "nan"):
            continue

        parse_result = parse(dvs_id, dvs_type, specification)
        parsed_rows.append(
            DVSRow(
                row_index=row_index,
                dvs_id=dvs_id,
                dvs_type=dvs_type,
                specification=str(specification).strip() if pd.notna(specification) else "",
                parse_result=parse_result,
            )
        )
    return parsed_rows


# ---------------------------------------------------------------------------
# Excel Writers
# ---------------------------------------------------------------------------


def _write_report_sheet(
    workbook: xlsxwriter.Workbook,
    parsed_rows: list[DVSRow],
    formats: dict,
) -> None:
    """Writes the detailed validation report sheet."""
    worksheet = workbook.add_worksheet(REPORT_SHEET_NAME)

    # Column layout: DVS ID | DVS Type | Status | Specification | Errors | Corrected Spec
    headers = [
        "DVS ID",
        "DVS Type",
        "Status",
        "Specification",
        "Error Code",
        "Error Description",
        "Suggestion",
        "Corrected Spec",
    ]
    column_widths = [20, 22, 10, 55, 28, 55, 55, 55]

    for col_index, (header_name, column_width) in enumerate(zip(headers, column_widths)):
        worksheet.set_column(col_index, col_index, column_width)
        worksheet.write(0, col_index, header_name, formats["header"])

    worksheet.freeze_panes(1, 0)
    worksheet.set_row(0, 20)

    current_row = 1
    for dvs_row in parsed_rows:
        parse_result = dvs_row.parse_result
        if parse_result.is_skipped:
            status_text = "NA"
            status_format = formats["skipped"]
        elif parse_result.is_valid:
            status_text = "VALID"
            status_format = formats["valid"]
        else:
            status_text = "INVALID"
            status_format = formats["invalid"]
        spec_format = formats["spec"] if parse_result.is_valid else formats["spec_error"]

        if not parse_result.errors:
            # Single row for valid entries
            worksheet.write(current_row, 0, dvs_row.dvs_id, formats["normal"])
            worksheet.write(current_row, 1, dvs_row.dvs_type, formats["normal"])
            worksheet.write(current_row, 2, status_text, status_format)
            worksheet.write(current_row, 3, dvs_row.specification, spec_format)
            worksheet.write(current_row, 4, "", formats["normal"])
            worksheet.write(current_row, 5, "", formats["normal"])
            worksheet.write(current_row, 6, "", formats["normal"])
            worksheet.write(current_row, 7, "", formats["normal"])
            current_row += 1
        else:
            # One row per error; merge DVS ID / Type / Status / Spec / Corrected cells vertically
            corrected_spec = parse_result.corrected_spec or ""
            row_span_start = current_row

            for error_index, dvs_error in enumerate(parse_result.errors):
                worksheet.write(current_row, 4, dvs_error.error_code, formats["error_code"])
                worksheet.write(current_row, 5, dvs_error.description, formats["normal"])
                worksheet.write(current_row, 6, dvs_error.suggestion, formats["suggestion"])
                current_row += 1

            row_span_end = current_row - 1

            if row_span_start == row_span_end:
                worksheet.write(row_span_start, 0, dvs_row.dvs_id, formats["normal"])
                worksheet.write(row_span_start, 1, dvs_row.dvs_type, formats["normal"])
                worksheet.write(row_span_start, 2, status_text, status_format)
                worksheet.write(row_span_start, 3, dvs_row.specification, spec_format)
                worksheet.write(row_span_start, 7, corrected_spec, formats["corrected"])
            else:
                worksheet.merge_range(row_span_start, 0, row_span_end, 0, dvs_row.dvs_id, formats["normal"])
                worksheet.merge_range(row_span_start, 1, row_span_end, 1, dvs_row.dvs_type, formats["normal"])
                worksheet.merge_range(row_span_start, 2, row_span_end, 2, status_text, status_format)
                worksheet.merge_range(row_span_start, 3, row_span_end, 3, dvs_row.specification, spec_format)
                worksheet.merge_range(row_span_start, 7, row_span_end, 7, corrected_spec, formats["corrected"])


def _write_summary_sheet(
    workbook: xlsxwriter.Workbook,
    parsed_rows: list[DVSRow],
    formats: dict,
) -> None:
    """Writes the summary sheet with aggregate statistics."""
    worksheet = workbook.add_worksheet(SUMMARY_SHEET_NAME)
    worksheet.set_column(0, 0, 32)
    worksheet.set_column(1, 1, 16)

    skipped_count = sum(1 for row in parsed_rows if row.parse_result.is_skipped)
    total_count = len(parsed_rows)
    checked_count = total_count - skipped_count
    valid_count = sum(1 for row in parsed_rows if row.parse_result.is_valid and not row.parse_result.is_skipped)
    invalid_count = checked_count - valid_count
    auto_correctable_count = sum(
        1 for row in parsed_rows if row.parse_result.corrected_spec is not None
    )

    # Count errors by error code
    error_code_counts: dict[str, int] = {}
    for dvs_row in parsed_rows:
        for dvs_error in dvs_row.parse_result.errors:
            error_code_counts[dvs_error.error_code] = (
                error_code_counts.get(dvs_error.error_code, 0) + 1
            )

    worksheet.write(0, 0, "DVS Specification Validation Summary", formats["summary_title"])
    worksheet.write(0, 1, "", formats["summary_title"])
    worksheet.set_row(0, 22)

    summary_rows = [
        ("Total DVS Entries", str(total_count)),
        ("Checked", str(checked_count)),
        ("NA (Skipped)", str(skipped_count)),
        ("Valid", str(valid_count)),
        ("Invalid", str(invalid_count)),
        ("Auto-correctable", str(auto_correctable_count)),
    ]

    for row_index, (label, value) in enumerate(summary_rows, start=1):
        worksheet.write(row_index, 0, label, formats["summary_label"])
        worksheet.write(row_index, 1, value, formats["summary_value"])

    if error_code_counts:
        error_table_start = 1 + len(summary_rows) + 1
        worksheet.write(error_table_start, 0, "Error Code", formats["subheader"])
        worksheet.write(error_table_start, 1, "Count", formats["subheader"])
        for row_index, (error_code, count) in enumerate(
            sorted(error_code_counts.items()), start=error_table_start + 1
        ):
            worksheet.write(row_index, 0, error_code, formats["summary_cell"])
            worksheet.write(row_index, 1, str(count), formats["summary_cell"])

    # Move Summary to first tab
    worksheet.activate()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_dvs_specifications(
    input_file: str,
    output_file: str | None = None,
    sheet_name: str = DEFAULT_SHEET_NAME,
    header_row: int = DEFAULT_HEADER_ROW,
    column_dvs_id: str = COLUMN_DVS_ID,
    column_dvs_type: str = COLUMN_DVS_TYPE,
    column_specification: str = COLUMN_SPECIFICATION,
) -> str:
    """
    Reads a DVS Excel file, validates all Specification entries,
    and writes a detailed validation report Excel file.

    Returns a summary message string.
    """
    if not output_file:
        base_name = os.path.splitext(input_file)[0]
        output_file = f"{base_name}_DVS_Validation.xlsx"

    dataframe = _read_dvs_dataframe(
        input_file, sheet_name, header_row,
        column_dvs_id, column_dvs_type, column_specification,
    )
    parsed_rows = _parse_all_rows(dataframe, column_dvs_id, column_dvs_type, column_specification)

    if not parsed_rows:
        return f"No DVS entries found in sheet '{sheet_name}'."

    total_count = len(parsed_rows)
    valid_count = sum(1 for row in parsed_rows if row.parse_result.is_valid)
    invalid_count = total_count - valid_count
    auto_correctable_count = sum(
        1 for row in parsed_rows if row.parse_result.corrected_spec is not None
    )

    workbook = xlsxwriter.Workbook(output_file)
    try:
        formats = create_formats(workbook)
        _write_summary_sheet(workbook, parsed_rows, formats)
        _write_report_sheet(workbook, parsed_rows, formats)
    finally:
        workbook.close()

    result_message = (
        f"DVS validation complete: {total_count} entries checked. "
        f"{valid_count} valid, {invalid_count} invalid"
    )
    if auto_correctable_count > 0:
        result_message += f" ({auto_correctable_count} auto-correctable)"
    result_message += f". Report saved to: {os.path.abspath(output_file)}"

    return result_message
