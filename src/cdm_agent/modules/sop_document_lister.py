import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import xlsxwriter

from cdm_agent.modules.excel_theme import create_formats


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SIGNED_KEYWORDS = ("final", "signed")
_SOP_PREFIX_PATTERN = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+_|^[A-Z0-9]+_", re.IGNORECASE)
_VERSION_FOLDER_PATTERN = re.compile(
    r"^[Vv]\d+(\.\d+)*$"   # V1.0, V2.0, V1.0.1
    r"|^[Ss][Ee][Qq]\d+$"  # SEQ001, SEQ002
    r"|^[Ss]\d+$"           # S001, S002
    r"|^\d+$",              # 001, 002, 003
)

# Companion document rules: when a trigger keyword is processed, also search for these documents.
# require_final=True  → file must contain 'final' (case-insensitive) in its stem
# require_final=False → any PDF matching the keyword is accepted
_COMPANION_RULES: dict[str, list[dict]] = {
    "Database Specifications": [
        {"keyword": "Annotated", "require_final": True},
    ],
    "CRF Approval Form": [
        {"keyword": "Blank eCRF", "require_final": True},
    ],
    "Data Mapping Specifications": [
        {"keyword": "Report", "require_final": True},
    ],
}

# Keywords whose PDFs do not carry signed/final markers — searched by keyword alone.
_KEYWORDS_WITHOUT_SIGNED_REQUIREMENT: set[str] = {"CRF Comparison Table"}

# Displayed when a file exists but its path contains no version folder.
_NO_VERSION_LABEL = "-"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_signed_documents(
    sop_log_file: str,
    target_folder: str,
    output_file: str | None = None,
) -> str:
    """
    Reads Appendix entries from an SOP Log Excel file, then searches for matching
    signed PDF files recursively in the target folder. Returns the path to the
    generated Excel report.

    Each row represents one (Appendix type × Version/Sequence) combination.
    Version is identified by the version folder name (V1.0, SEQ001, etc.) found
    in the file's path. If no version folder exists, Version is shown as "-".
    """
    target_path = Path(target_folder)
    sop_entries = _parse_sop_log(sop_log_file)
    all_file_paths = [path for path in target_path.rglob("*") if path.is_file()]
    all_pdf_paths = [path for path in all_file_paths if path.suffix.lower() == ".pdf"]

    flat_rows: list[dict] = []
    for entry in sop_entries:
        flat_rows.extend(
            _rows_for_keyword(entry["keyword"], all_file_paths, all_pdf_paths, target_path)
        )

    if not output_file:
        base_name = Path(sop_log_file).stem
        output_file = os.path.join(
            os.path.dirname(sop_log_file),
            f"{base_name}_Document_List_{datetime.now().strftime('%Y%m%d')}.xlsx",
        )

    _write_excel(flat_rows, output_file)
    return os.path.abspath(output_file)


# ---------------------------------------------------------------------------
# SOP Log Parsing
# ---------------------------------------------------------------------------

def _parse_sop_log(sop_log_file: str) -> list[dict]:
    """
    Parses the SOP Log Excel and returns deduplicated Appendix entries.
    Each entry contains: keyword (str) extracted from the Title column.
    """
    excel_file = pd.ExcelFile(sop_log_file)
    sheet_name = excel_file.sheet_names[0]
    dataframe = pd.read_excel(excel_file, sheet_name=sheet_name, header=2)

    type_column = _find_column(dataframe, "Type")
    title_column = _find_column(dataframe, "Title")

    appendix_mask = dataframe[type_column].astype(str).str.strip().str.lower() != "sop"
    filtered = dataframe[appendix_mask][[title_column]].dropna(subset=[title_column])

    seen_keywords: set[str] = set()
    entries: list[dict] = []
    for _, row in filtered.iterrows():
        title = str(row[title_column]).strip()
        keyword = _extract_keyword(title)
        if keyword not in seen_keywords:
            seen_keywords.add(keyword)
            entries.append({"keyword": keyword})

    return entries


def _find_column(dataframe: pd.DataFrame, target_name: str) -> str:
    """Finds the column name matching target_name (case-insensitive)."""
    for column in dataframe.columns:
        if str(column).strip().lower() == target_name.lower():
            return column
    raise ValueError(
        f"Column '{target_name}' not found. Available columns: {list(dataframe.columns)}"
    )


def _extract_keyword(title: str) -> str:
    """
    Strips the SOP number prefix from a title string.
    Examples:
      'JS8700-A1_Note to File' -> 'Note to File'
      'JS8700_Project Management' -> 'Project Management'
      'Data Management Plan' -> 'Data Management Plan'
    """
    return _SOP_PREFIX_PATTERN.sub("", title).strip()


# ---------------------------------------------------------------------------
# Filename Normalization
# ---------------------------------------------------------------------------

def _normalize_for_matching(text: str) -> str:
    """
    Normalizes a string for filename matching by replacing separators
    (underscores, hyphens, whitespace) with a single space and lowercasing.
    """
    return re.sub(r'[\s_\-]+', ' ', text).lower().strip()


# ---------------------------------------------------------------------------
# Version Folder Detection
# ---------------------------------------------------------------------------

def _find_version_folder_in_path(
    file_path: Path,
    target_path: Path,
) -> tuple[str, str | None] | None:
    """
    Returns (version_folder_name, parent_folder_name) for the version/sequence folder
    found in the path, scanning from the file outward toward target_path.
    parent_folder_name is the folder directly above the version folder (relative to
    target_path), or None if the version folder is directly under target_path.
    Returns None if no version folder is found.
    Examples:
      target/01. DMP/V1.0/file.pdf     -> ('V1.0', '01. DMP')
      target/01. DMP/V1.0/working/file -> ('V1.0', '01. DMP')
      target/V1.0/file.pdf             -> ('V1.0', None)
      target/01. DMP/file.pdf          -> None
    """
    try:
        relative_parts = file_path.relative_to(target_path).parts[:-1]  # exclude filename
    except ValueError:
        return None

    for index, part in enumerate(reversed(relative_parts)):
        if _VERSION_FOLDER_PATTERN.match(part):
            original_index = len(relative_parts) - 1 - index
            parent_name = relative_parts[original_index - 1] if original_index > 0 else None
            return (part, parent_name)
    return None


def _version_display_label(version_key: tuple[str, str | None] | None) -> str:
    """
    Returns the display string for the Version column.
    Examples:
      ('V1.0', '01. DMP')  -> 'V1.0 (01. DMP)'
      ('V1.0', None)       -> 'V1.0'
      None                 -> '-'
    """
    if version_key is None:
        return _NO_VERSION_LABEL
    version_name, parent_name = version_key
    if parent_name:
        return f"{version_name} ({parent_name})"
    return version_name


# ---------------------------------------------------------------------------
# Row Builder
# ---------------------------------------------------------------------------

def _rows_for_keyword(
    keyword: str,
    all_file_paths: list[Path],
    all_pdf_paths: list[Path],
    target_path: Path,
) -> list[dict]:
    """
    Returns flat rows for a single SOP keyword.
    Groups matching files by their version folder, then checks for signed PDFs
    in each version group. Companion rules are appended as additional rows.
    """
    matching_files = _find_matching_files(all_file_paths, keyword)

    if not matching_files:
        return [_warning_row(keyword, "", "No signed document found")]

    version_groups = _group_by_version(matching_files, target_path)
    rows: list[dict] = []

    for version_key in sorted(version_groups.keys(), key=lambda key: ("" if key is None else key[0])):
        version_label = _version_display_label(version_key)
        require_signed = keyword not in _KEYWORDS_WITHOUT_SIGNED_REQUIREMENT
        signed_pdfs = _find_signed_pdfs_for_version(
            all_pdf_paths, version_key, keyword, target_path, require_signed
        )

        if signed_pdfs:
            for pdf_path in signed_pdfs:
                rows.append({
                    "title": keyword,
                    "version": version_label,
                    "file_name": pdf_path.name,
                    "comment": "",
                    "has_warning": False,
                })
        else:
            rows.append(_warning_row(keyword, version_label, "No signed document found"))

    if keyword in _COMPANION_RULES:
        rows.extend(_companion_rows(keyword, all_file_paths, all_pdf_paths, target_path))

    return rows


def _companion_rows(
    trigger_keyword: str,
    all_file_paths: list[Path],
    all_pdf_paths: list[Path],
    target_path: Path,
) -> list[dict]:
    """Resolves companion document rules and returns flat rows."""
    rows: list[dict] = []
    for rule in _COMPANION_RULES[trigger_keyword]:
        companion_keyword = rule["keyword"]
        require_final = rule["require_final"]
        combined_title = f"{trigger_keyword} ({companion_keyword})"

        matching_files = _find_matching_files(all_file_paths, companion_keyword)

        if not matching_files:
            final_note = " (Final)" if require_final else ""
            rows.append(_warning_row(combined_title, "", f"No {companion_keyword}{final_note} found"))
            continue

        version_groups = _group_by_version(matching_files, target_path)
        for version_key in sorted(version_groups.keys(), key=lambda key: ("" if key is None else key[0])):
            version_label = _version_display_label(version_key)
            signed_pdfs = _find_signed_pdfs_for_version(
                all_pdf_paths, version_key, companion_keyword, target_path, require_final
            )
            if signed_pdfs:
                for pdf_path in signed_pdfs:
                    rows.append({
                        "title": combined_title,
                        "version": version_label,
                        "file_name": pdf_path.name,
                        "comment": "",
                        "has_warning": False,
                    })
            else:
                final_note = " (Final)" if require_final else ""
                rows.append(
                    _warning_row(combined_title, version_label, f"No {companion_keyword}{final_note} found")
                )

    return rows


def _warning_row(keyword: str, version_label: str, message: str) -> dict:
    """Builds a flat row representing a missing or problematic document."""
    return {
        "title": keyword,
        "version": version_label,
        "file_name": "",
        "comment": message,
        "has_warning": True,
    }


# ---------------------------------------------------------------------------
# Search Helpers
# ---------------------------------------------------------------------------

def _find_matching_files(all_file_paths: list[Path], keyword: str) -> list[Path]:
    """
    Returns files (any type) whose normalized stem contains the normalized keyword.
    """
    normalized_keyword = _normalize_for_matching(keyword)
    return [
        file_path for file_path in all_file_paths
        if normalized_keyword in _normalize_for_matching(file_path.stem)
    ]


def _group_by_version(
    file_paths: list[Path],
    target_path: Path,
) -> dict[tuple[str, str | None] | None, list[Path]]:
    """
    Groups file paths by their (version_folder_name, parent_folder_name) key.
    Files with no version folder in their path are grouped under None.
    """
    groups: dict[tuple[str, str | None] | None, list[Path]] = {}
    for file_path in file_paths:
        version_key = _find_version_folder_in_path(file_path, target_path)
        if version_key not in groups:
            groups[version_key] = []
        groups[version_key].append(file_path)
    return groups


def _find_signed_pdfs_for_version(
    all_pdf_paths: list[Path],
    version_key: tuple[str, str | None] | None,
    keyword: str,
    target_path: Path,
    require_signed: bool,
) -> list[Path]:
    """
    Returns PDFs that:
    - Belong to the given version key (version_folder_name, parent_folder_name)
    - Have a stem matching the keyword (normalized)
    - Contain a signed/final marker if require_signed is True
    """
    normalized_keyword = _normalize_for_matching(keyword)
    matched = []
    for pdf_path in all_pdf_paths:
        if normalized_keyword not in _normalize_for_matching(pdf_path.stem):
            continue
        if require_signed and not any(marker in pdf_path.stem.lower() for marker in _SIGNED_KEYWORDS):
            continue
        pdf_version_key = _find_version_folder_in_path(pdf_path, target_path)
        if pdf_version_key == version_key:
            matched.append(pdf_path)
    return matched


# ---------------------------------------------------------------------------
# Excel Output
# ---------------------------------------------------------------------------

_COL_WIDTHS = {
    "Appendix 종류": 30,
    "Version": 30,
    "문서명": 70,
    "Comment": 45,
}
_COLUMNS = list(_COL_WIDTHS.keys())


def _write_excel(flat_rows: list[dict], output_file: str) -> None:
    """Writes flat rows to an Excel file. One row per (Appendix type × Version)."""
    workbook = xlsxwriter.Workbook(output_file)
    formats = create_formats(workbook)
    wrap_format = workbook.add_format({
        "border": 1,
        "text_wrap": True,
        "valign": "top",
        "align": "left",
        "font_name": "Malgun Gothic",
    })

    worksheet = workbook.add_worksheet("Document List")
    worksheet.freeze_panes(1, 0)

    for col_index, col_name in enumerate(_COLUMNS):
        worksheet.set_column(col_index, col_index, _COL_WIDTHS[col_name])
        worksheet.write(0, col_index, col_name, formats["header"])

    for row_index, row in enumerate(flat_rows, start=1):
        comment_format = formats["warning"] if row["has_warning"] else wrap_format
        worksheet.write(row_index, 0, row["title"], wrap_format)
        worksheet.write(row_index, 1, row["version"], wrap_format)
        worksheet.write(row_index, 2, row["file_name"], wrap_format)
        worksheet.write(row_index, 3, row["comment"], comment_format)
        worksheet.set_row(row_index, 18)

    workbook.close()
