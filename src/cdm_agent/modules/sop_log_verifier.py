import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber
import xlsxwriter

from cdm_agent.modules.excel_theme import create_formats


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DOC_NUMBER_PATTERN = re.compile(r"^([A-Z]{2}\d[\w-]+?)_", re.IGNORECASE)
_SOP_INDEX_SECTION_PATTERN = re.compile(r"index\s+of\s+sop", re.IGNORECASE)
_APPENDIX_INDEX_SECTION_PATTERN = re.compile(r"index\s+of\s+appendix", re.IGNORECASE)
# Matches: row_number  DOC-NO  Version  ...text...  YYYY-MM-DD  Status
_INDEX_ENTRY_PATTERN = re.compile(
    r"^\d+\s+([A-Z]{2}\d[\w-]+)\s+(V[\d.]+)\s+.+?\s+(\d{4}-\d{2}-\d{2})\s+\S+",
    re.IGNORECASE,
)

_COL_WIDTHS = {
    "Document No.": 15,
    "Title": 35,
    "Excel Version": 15,
    "Excel Eff. Date": 15,
    "PDF Version": 15,
    "PDF Eff. Date": 15,
    "Result": 25,
}
_COLUMNS = list(_COL_WIDTHS.keys())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_sop_log_against_index(
    sop_log_file: str,
    sop_index_file: str,
    output_file: str | None = None,
) -> str:
    """
    Compares the SOP Log Excel against the SOP Index PDF and reports version
    and effective date discrepancies for both SOP and Appendix entries.
    Returns the path to the generated Excel report.
    """
    pdf_sop_index, pdf_appendix_index = _parse_sop_index_pdf(sop_index_file)
    excel_sop_entries, excel_appendix_entries = _parse_sop_log_excel(sop_log_file)

    sop_rows = _build_comparison_rows(excel_sop_entries, pdf_sop_index)
    appendix_rows = _build_comparison_rows(excel_appendix_entries, pdf_appendix_index)

    if not output_file:
        base_name = Path(sop_log_file).stem
        output_file = os.path.join(
            os.path.dirname(sop_log_file),
            f"{base_name}_Verification_{datetime.now().strftime('%Y%m%d')}.xlsx",
        )

    _write_excel(sop_rows, appendix_rows, output_file)
    return os.path.abspath(output_file)


# ---------------------------------------------------------------------------
# PDF Parsing
# ---------------------------------------------------------------------------

def _parse_sop_index_pdf(
    sop_index_file: str,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """
    Extracts SOP and Appendix index entries from the SOP Index PDF.
    Returns two dicts: (sop_index, appendix_index)
    Each dict maps doc_no -> {ver, effective_date}.
    """
    full_text = _extract_pdf_text(sop_index_file)
    sop_index: dict[str, dict] = {}
    appendix_index: dict[str, dict] = {}
    _parse_index_sections(full_text, sop_index, appendix_index)
    return sop_index, appendix_index


def _extract_pdf_text(pdf_file: str) -> str:
    """Extracts and concatenates all text from a PDF file."""
    lines: list[str] = []
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                lines.append(page_text)
    return "\n".join(lines)


def _parse_index_sections(
    full_text: str,
    sop_index: dict[str, dict],
    appendix_index: dict[str, dict],
) -> None:
    """
    Splits PDF text into SOP and Appendix sections, then parses each section's
    index entries into the provided dicts.
    """
    sop_section_start = None
    appendix_section_start = None

    text_lines = full_text.splitlines()
    for line_index, line in enumerate(text_lines):
        if _SOP_INDEX_SECTION_PATTERN.search(line) and sop_section_start is None:
            sop_section_start = line_index
        elif _APPENDIX_INDEX_SECTION_PATTERN.search(line) and appendix_section_start is None:
            appendix_section_start = line_index

    if sop_section_start is not None:
        end = appendix_section_start if appendix_section_start is not None else len(text_lines)
        sop_section_lines = text_lines[sop_section_start:end]
        _extract_entries_from_lines(sop_section_lines, sop_index)

    if appendix_section_start is not None:
        appendix_section_lines = text_lines[appendix_section_start:]
        _extract_entries_from_lines(appendix_section_lines, appendix_index)


def _extract_entries_from_lines(
    section_lines: list[str],
    target_index: dict[str, dict],
) -> None:
    """
    Parses index entry lines and populates target_index.
    Later entries override earlier ones (last occurrence wins).
    """
    for line in section_lines:
        match = _INDEX_ENTRY_PATTERN.match(line.strip())
        if match:
            doc_no = match.group(1).upper()
            version = match.group(2)
            effective_date = match.group(3)
            target_index[doc_no] = {
                "ver": version,
                "effective_date": effective_date,
            }


# ---------------------------------------------------------------------------
# Excel SOP Log Parsing
# ---------------------------------------------------------------------------

def _parse_sop_log_excel(
    sop_log_file: str,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """
    Parses the SOP Log Excel and returns (sop_entries, appendix_entries).
    Each entry maps doc_no -> {ver, effective_date, title}.
    Uses LAST occurrence per doc_no as the current applied version.
    """
    excel_file = pd.ExcelFile(sop_log_file)
    sheet_name = excel_file.sheet_names[0]
    dataframe = pd.read_excel(excel_file, sheet_name=sheet_name, header=2)

    type_column = _find_column(dataframe, "Type")
    title_column = _find_column(dataframe, "Title")
    version_column = _find_column_any(dataframe, ["Version", "Applied Version"])
    effective_date_column = _find_column(dataframe, "Effective Date")

    sop_entries: dict[str, dict] = {}
    appendix_entries: dict[str, dict] = {}

    for _, row in dataframe.iterrows():
        row_type = str(row.get(type_column, "")).strip()
        title = str(row.get(title_column, "")).strip()
        version = str(row.get(version_column, "")).strip()
        effective_date_raw = row.get(effective_date_column)

        if not title or title == "nan":
            continue

        doc_no = _extract_doc_number(title)
        if not doc_no:
            continue

        effective_date = _normalize_date(effective_date_raw)

        entry = {
            "ver": version if version != "nan" else "",
            "effective_date": effective_date,
            "title": title,
        }

        if row_type.strip().lower() == "sop":
            sop_entries[doc_no] = entry
        else:
            appendix_entries[doc_no] = entry

    return sop_entries, appendix_entries


def _find_column(dataframe: pd.DataFrame, target_name: str) -> str:
    """Finds the column name matching target_name (case-insensitive)."""
    for column in dataframe.columns:
        if str(column).strip().lower() == target_name.lower():
            return column
    raise ValueError(
        f"Column '{target_name}' not found. Available columns: {list(dataframe.columns)}"
    )


def _find_column_any(dataframe: pd.DataFrame, candidate_names: list[str]) -> str:
    """Returns the first matching column from a list of candidate names (case-insensitive)."""
    for candidate in candidate_names:
        for column in dataframe.columns:
            if str(column).strip().lower() == candidate.lower():
                return column
    raise ValueError(
        f"None of {candidate_names} found. Available columns: {list(dataframe.columns)}"
    )


def _extract_doc_number(title: str) -> str | None:
    """
    Extracts the document number prefix from a title string.
    Examples:
      'JS8700-A1_Note to File'      -> 'JS8700-A1'
      'JS8700_Project Management'   -> 'JS8700'
      'Data Management Plan'        -> None
    """
    match = _DOC_NUMBER_PATTERN.match(title.strip())
    if match:
        return match.group(1).upper()
    return None


def _normalize_date(effective_date_raw) -> str:
    """
    Converts a date value (datetime, Timestamp, or string) to 'YYYY-MM-DD' format.
    Returns empty string if conversion fails.
    """
    if pd.isna(effective_date_raw):
        return ""
    if hasattr(effective_date_raw, "strftime"):
        return effective_date_raw.strftime("%Y-%m-%d")
    date_str = str(effective_date_raw).strip()
    # Already in YYYY-MM-DD format
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return date_str
    return date_str


# ---------------------------------------------------------------------------
# Comparison Logic
# ---------------------------------------------------------------------------

def _build_comparison_rows(
    excel_entries: dict[str, dict],
    pdf_index: dict[str, dict],
) -> list[dict]:
    """
    For each Excel entry, compares against the PDF index and builds result rows.
    """
    rows: list[dict] = []
    for doc_no, excel_entry in excel_entries.items():
        pdf_entry = pdf_index.get(doc_no)

        if pdf_entry is None:
            result = "⚠ Not found in PDF"
            pdf_ver = ""
            pdf_date = ""
        else:
            version_matches = excel_entry["ver"] == pdf_entry["ver"]
            date_matches = excel_entry["effective_date"] == pdf_entry["effective_date"]
            result = _build_result_label(version_matches, date_matches)
            pdf_ver = pdf_entry["ver"]
            pdf_date = pdf_entry["effective_date"]

        rows.append({
            "doc_no": doc_no,
            "title": excel_entry["title"],
            "excel_ver": excel_entry["ver"],
            "excel_date": excel_entry["effective_date"],
            "pdf_ver": pdf_ver,
            "pdf_date": pdf_date,
            "result": result,
        })

    return rows


def _build_result_label(version_matches: bool, date_matches: bool) -> str:
    """Returns the Result cell value based on match flags."""
    if version_matches and date_matches:
        return "✓"
    if not version_matches and not date_matches:
        return "⚠ Version & Date mismatch"
    if not version_matches:
        return "⚠ Version mismatch"
    return "⚠ Date mismatch"


# ---------------------------------------------------------------------------
# Excel Output
# ---------------------------------------------------------------------------

def _write_excel(
    sop_rows: list[dict],
    appendix_rows: list[dict],
    output_file: str,
) -> None:
    """Writes the verification results to a two-sheet Excel file."""
    workbook = xlsxwriter.Workbook(output_file)
    formats = create_formats(workbook)

    _write_sheet(workbook, formats, "SOP", sop_rows)
    _write_sheet(workbook, formats, "Appendix", appendix_rows)

    workbook.close()


def _write_sheet(
    workbook: xlsxwriter.Workbook,
    formats: dict,
    sheet_name: str,
    rows: list[dict],
) -> None:
    """Writes a single verification sheet."""
    worksheet = workbook.add_worksheet(sheet_name)
    worksheet.freeze_panes(1, 0)

    for col_index, col_name in enumerate(_COLUMNS):
        worksheet.set_column(col_index, col_index, _COL_WIDTHS[col_name])
        worksheet.write(0, col_index, col_name, formats["header"])

    for row_index, result_row in enumerate(rows, start=1):
        result = result_row["result"]
        result_format = formats["valid"] if result == "✓" else formats["warning"]

        worksheet.write(row_index, 0, result_row["doc_no"], formats["normal"])
        worksheet.write(row_index, 1, result_row["title"], formats["normal"])
        worksheet.write(row_index, 2, result_row["excel_ver"], formats["normal"])
        worksheet.write(row_index, 3, result_row["excel_date"], formats["normal"])
        worksheet.write(row_index, 4, result_row["pdf_ver"], formats["normal"])
        worksheet.write(row_index, 5, result_row["pdf_date"], formats["normal"])
        worksheet.write(row_index, 6, result, result_format)
