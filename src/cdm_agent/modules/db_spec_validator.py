"""
DB Specification Validator

Validates a DB Specification Excel file against CDASH standards (CDASHIG v2.3 / SDTMIG v3.4).

Checks:
  1. Domain validity            — Domain Infos sheet
  2. ITEM ID suffix vs TYPE     — Database Specifications + DB Specifications Table Detail
  3. CODE field duplicate codes — Database Specifications + DB Specifications Table Detail
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import xlsxwriter

from cdm_agent.modules.cdash_reference_generator import load_cdash_variables
from cdm_agent.modules.excel_theme import create_formats

# ---------------------------------------------------------------------------
# CDASH Reference Data (loaded from JSON)
# ---------------------------------------------------------------------------

# suffixes:   {suffix: expected_type}  — stripped from --XXX CDASH Model variables
# standalone: {variable_name: type}    — non-prefixed CDASH Model variables
# domains:    {"cdashig": [...], "sdtmig_only": [...]}
_CDASH_SUFFIXES, _CDASH_STANDALONE, _CDASH_DOMAINS = load_cdash_variables()

_CDASHIG_DOMAINS: frozenset[str] = frozenset(_CDASH_DOMAINS.get("cdashig", []))
_SDTMIG_ONLY_DOMAINS: frozenset[str] = frozenset(_CDASH_DOMAINS.get("sdtmig_only", []))

# Sponsor-defined domains are not in any standard — kept as a fixed set.
_SPONSOR_DEFINED_DOMAINS: frozenset[str] = frozenset({"EN", "RN", "PG", "SN"})

CODELIST_LAYOUTS: set[str] = {"RADIO", "CHECK", "DROPDOWN"}

# ---------------------------------------------------------------------------
# Error Type Codes
# ---------------------------------------------------------------------------

# Domain check
ERR_UNKNOWN_DOMAIN = "ERR_UNKNOWN_DOMAIN"
WARN_SDTMIG_ONLY_DOMAIN = "WARN_SDTMIG_ONLY_DOMAIN"
WARN_SPONSOR_DEFINED_DOMAIN = "WARN_SPONSOR_DEFINED_DOMAIN"

# Suffix / TYPE check — all WARNING level
WARN_NAMING_CONVENTION = "WARN_NAMING_CONVENTION"
WARN_UNKNOWN_SUFFIX = "WARN_UNKNOWN_SUFFIX"
WARN_TYPE_MISMATCH = "WARN_TYPE_MISMATCH"

# Code duplicate check
ERR_DUPLICATE_CODE = "ERR_DUPLICATE_CODE"

CHAR_TYPES: set[str] = {"NVARCHAR", "NCHAR"}
NUM_TYPES: set[str] = {"INTEGER", "DECIMAL"}

_TYPE_PATTERN = re.compile(r"^(NVARCHAR|NCHAR|INTEGER|DECIMAL)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DomainValidationResult:
    domain: str
    base_domain: str
    result: str             # "PASS" | "WARNING" | "FAIL"
    error_type: str | None  # None when PASS
    reason: str


@dataclass
class SuffixTypeResult:
    source_sheet: str
    domain: str
    item_id: str
    layout: str
    actual_type: str
    actual_max_len: int | None
    matched_suffix: str | None
    expected_type_category: str | None   # "Char" | "Num"
    result: str             # "PASS" | "WARNING"
    error_type: str | None  # None when PASS
    reason: str


@dataclass
class CodeDuplicateResult:
    source_sheet: str
    item_id: str
    code_text: str
    duplicate_codes: list[int] = field(default_factory=list)
    result: str = "PASS"
    error_type: str | None = None  # None when PASS
    reason: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _strip_numeric_suffix(domain: str) -> str:
    """Remove trailing digits from domain name. VS01 → VS, LB03 → LB."""
    return re.sub(r"\d+$", "", domain.strip().upper())


def _extract_base_type(type_value: str) -> str:
    """Extract base type keyword from a TYPE cell value. NVARCHAR(10) → NVARCHAR."""
    match = _TYPE_PATTERN.match(str(type_value).strip())
    return match.group(1).upper() if match else str(type_value).strip().upper()


def _extract_max_len(type_value: str) -> int | None:
    """Extract numeric length from TYPE cell. NVARCHAR(10) → 10."""
    match = re.search(r"\((\d+)", str(type_value))
    return int(match.group(1)) if match else None


def _lookup_suffix(remainder: str) -> tuple[str | None, str | None]:
    """Return (suffix, type_category) if remainder exactly equals a known CDASH suffix."""
    if remainder in _CDASH_SUFFIXES:
        return remainder, _CDASH_SUFFIXES[remainder]
    return None, None


def _extract_suffix(item_id: str, domain: str) -> tuple[str | None, str | None, bool]:
    """
    Extract CDASH suffix and expected type category from an ITEM ID.

    Returns (matched_suffix, type_category, domain_found) where:
      - domain_found=True  : domain prefix was located and used for parsing
      - domain_found=False : domain not in ITEM ID; fell back to standalone lookup

    Strategy:
      1. Candidate = part after last '_' if present (horizontal generic), else full ITEM ID.
      2. Strip domain prefix (full domain first, then base domain) → remainder must equal suffix.
      3. If domain not found: check CDASH standalone variables (e.g. BRTHDAT, SEX, VISITNUM).

    Examples:
      LBORRES   (domain=LB)   → strip LB   → ORRES  → ("ORRES", "Char", True)
      LB01ORRES (domain=LB)   → strip LB   → 01ORRES → (None, None, True)   ← naming violation
      LB01ORRES (domain=LB01) → strip LB01 → ORRES  → ("ORRES", "Char", True)
      VSORRES   (domain=VS01) → strip VS01? no → strip VS → ORRES → ("ORRES", "Char", True)
      BRTHDAT   (domain=DM)   → no prefix match → standalone → ("BRTHDAT", "Char", False)
      VISITNUM  (domain=VS)   → no prefix match → standalone → ("VISITNUM", "Num", False)
      XYZABC    (domain=AE)   → no prefix match, not standalone → (None, None, False)
    """
    item_upper = item_id.upper()
    domain_upper = domain.upper().strip()
    base_domain = _strip_numeric_suffix(domain_upper)

    candidate = item_upper.split("_")[-1] if "_" in item_upper else item_upper

    # Build prefix candidates: full domain first, then base domain (if different)
    prefixes: list[str] = [domain_upper]
    if base_domain and base_domain != domain_upper:
        prefixes.append(base_domain)

    for prefix in prefixes:
        if not prefix or not candidate.startswith(prefix):
            continue
        remainder = candidate[len(prefix):]
        if not remainder:
            continue
        matched_suffix, type_category = _lookup_suffix(remainder)
        # Domain prefix was found — authoritative parse regardless of whether suffix is known.
        return matched_suffix, type_category, True

    # Domain not found in candidate.
    # Check if candidate is a known CDASH standalone variable (no domain prefix by design).
    if candidate in _CDASH_STANDALONE:
        return candidate, _CDASH_STANDALONE[candidate], False

    # Truly unrecognized — naming convention violation.
    return None, None, False


def _parse_code_numbers(code_text: str) -> list[int]:
    """Extract [n] code numbers from a CODE field string."""
    return [int(m) for m in re.findall(r"\[(\d+)\]", str(code_text))]


def _normalize_str(value) -> str:
    """Convert cell value to stripped string, treating NaN/None as empty."""
    text = str(value).strip() if value is not None else ""
    return "" if text.lower() == "nan" else text


# ---------------------------------------------------------------------------
# Check 1: Domain Validity
# ---------------------------------------------------------------------------

def _validate_domains(dataframe: pd.DataFrame) -> list[DomainValidationResult]:
    results: list[DomainValidationResult] = []

    domain_col = next((c for c in dataframe.columns if c.strip().upper() == "DOMAIN"), None)
    if domain_col is None:
        return results

    seen_domains: set[str] = set()

    for _, row in dataframe.iterrows():
        raw_domain = _normalize_str(row.get(domain_col, ""))
        if not raw_domain:
            continue
        if raw_domain in seen_domains:
            continue
        seen_domains.add(raw_domain)

        base_domain = _strip_numeric_suffix(raw_domain)

        if base_domain in _CDASHIG_DOMAINS:
            result = "PASS"
            error_type = None
            reason = "CDASHIG v2.3 domain"
        elif base_domain in _SDTMIG_ONLY_DOMAINS:
            result = "WARNING"
            error_type = WARN_SDTMIG_ONLY_DOMAIN
            reason = "SDTMIG v3.4 only — not in CDASHIG v2.3"
        elif base_domain in _SPONSOR_DEFINED_DOMAINS:
            result = "WARNING"
            error_type = WARN_SPONSOR_DEFINED_DOMAIN
            reason = "Sponsor-defined domain — not in CDASH/SDTM standard"
        else:
            result = "FAIL"
            error_type = ERR_UNKNOWN_DOMAIN
            reason = f"Unrecognized domain '{base_domain}'"

        results.append(DomainValidationResult(
            domain=raw_domain,
            base_domain=base_domain,
            result=result,
            error_type=error_type,
            reason=reason,
        ))

    return results


# ---------------------------------------------------------------------------
# Check 2: ITEM ID Suffix vs TYPE + MAX_LEN
# ---------------------------------------------------------------------------

def _validate_suffix_types(dataframe: pd.DataFrame, source_sheet: str) -> list[SuffixTypeResult]:
    results: list[SuffixTypeResult] = []

    item_id_col = next((c for c in dataframe.columns if c.strip().upper() == "ITEM ID"), None)
    type_col = next((c for c in dataframe.columns if c.strip().upper() == "TYPE"), None)
    layout_col = next((c for c in dataframe.columns if c.strip().upper() == "LAYOUT"), None)
    domain_col = next((c for c in dataframe.columns if c.strip().upper() == "DOMAIN"), None)

    if not all([item_id_col, type_col]):
        return results

    # DOMAIN column may have empty cells in continuation rows (merged-cell style).
    # Forward-fill to carry the domain value down to all rows in the same group.
    if domain_col:
        dataframe = dataframe.copy()
        dataframe[domain_col] = dataframe[domain_col].ffill()

    for _, row in dataframe.iterrows():
        item_id = _normalize_str(row.get(item_id_col, ""))
        if not item_id:
            continue

        type_raw = _normalize_str(row.get(type_col, ""))
        layout = _normalize_str(row.get(layout_col, "")) if layout_col else ""
        domain = _normalize_str(row.get(domain_col, "")) if domain_col else ""
        base_type = _extract_base_type(type_raw)
        actual_max_len = _extract_max_len(type_raw)

        if layout.upper() in CODELIST_LAYOUTS:
            continue

        matched_suffix, type_category, domain_found = _extract_suffix(item_id, domain)

        # --- Case 1 & 2: suffix not identified ---
        if matched_suffix is None:
            result_value = "WARNING"
            expected_type_category = None
            if domain_found:
                # Case 1: domain prefix found but the remainder is not a CDASH suffix
                error_type_value = WARN_UNKNOWN_SUFFIX
                reason = (
                    f"Domain prefix found in '{item_id}' but remaining text "
                    "is not a recognized CDASH suffix"
                )
            else:
                # Case 2: domain prefix not found in ITEM ID at all
                error_type_value = WARN_NAMING_CONVENTION
                reason = (
                    f"ITEM ID naming convention violation: "
                    f"domain prefix '{domain}' not found in '{item_id}'"
                )

        # --- Case 3 & 4: suffix identified — check TYPE consistency ---
        else:
            expected_type_category = type_category
            type_matched = (
                (type_category == "Char" and base_type in CHAR_TYPES)
                or (type_category == "Num" and base_type in NUM_TYPES)
            )
            if type_matched:
                # Case 3: suffix and TYPE both match
                result_value = "PASS"
                error_type_value = None
                reason = f"Suffix '{matched_suffix}' expects {type_category} — TYPE matches"
            else:
                # Case 4: suffix found but TYPE does not match
                result_value = "WARNING"
                error_type_value = WARN_TYPE_MISMATCH
                reason = (
                    f"Suffix '{matched_suffix}' expects {type_category} "
                    f"but TYPE is '{base_type}'"
                )

        results.append(SuffixTypeResult(
            source_sheet=source_sheet,
            domain=domain,
            item_id=item_id,
            layout=layout,
            actual_type=type_raw,
            actual_max_len=actual_max_len,
            matched_suffix=matched_suffix,
            expected_type_category=expected_type_category,
            result=result_value,
            error_type=error_type_value,
            reason=reason,
        ))

    return results


# ---------------------------------------------------------------------------
# Check 3: CODE Duplicate
# ---------------------------------------------------------------------------

def _validate_code_duplicates(dataframe: pd.DataFrame, source_sheet: str) -> list[CodeDuplicateResult]:
    results: list[CodeDuplicateResult] = []

    item_id_col = next((c for c in dataframe.columns if c.strip().upper() == "ITEM ID"), None)
    code_col = next((c for c in dataframe.columns if c.strip().upper() == "CODE"), None)

    if not all([item_id_col, code_col]):
        return results

    for _, row in dataframe.iterrows():
        item_id = _normalize_str(row.get(item_id_col, ""))
        code_text = _normalize_str(row.get(code_col, ""))

        if not item_id or not code_text:
            continue

        code_numbers = _parse_code_numbers(code_text)
        if not code_numbers:
            continue

        seen: set[int] = set()
        duplicates: list[int] = []
        for number in code_numbers:
            if number in seen:
                if number not in duplicates:
                    duplicates.append(number)
            seen.add(number)

        if duplicates:
            result = "FAIL"
            error_type = ERR_DUPLICATE_CODE
            reason = f"Duplicate code numbers: {duplicates}"
        else:
            result = "PASS"
            error_type = None
            reason = "All code numbers are unique"

        results.append(CodeDuplicateResult(
            source_sheet=source_sheet,
            item_id=item_id,
            code_text=code_text,
            duplicate_codes=duplicates,
            result=result,
            error_type=error_type,
            reason=reason,
        ))

    return results


# ---------------------------------------------------------------------------
# Excel Report Writers
# ---------------------------------------------------------------------------

_RESULT_FORMAT_KEY = {"PASS": "pass", "WARNING": "warning", "FAIL": "fail"}


def _write_summary_sheet(
    worksheet: xlsxwriter.worksheet.Worksheet,
    domain_results: list[DomainValidationResult],
    suffix_results: list[SuffixTypeResult],
    code_results: list[CodeDuplicateResult],
    formats: dict,
) -> None:
    worksheet.set_column(0, 0, 30)
    worksheet.set_column(1, 4, 14)

    headers = ["Check", "Total", "PASS", "WARNING", "FAIL"]
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, formats["header"])

    check_data = [
        (
            "1. Domain Validity",
            domain_results,
            lambda r: r.result,
        ),
        (
            "2. Suffix / Type Check",
            suffix_results,
            lambda r: r.result,
        ),
        (
            "3. Code Duplicate Check",
            code_results,
            lambda r: r.result,
        ),
    ]

    all_results: list[str] = []

    for row_idx, (label, result_list, get_result) in enumerate(check_data, start=1):
        result_values = [get_result(r) for r in result_list]
        all_results.extend(result_values)

        counts = {
            "PASS": result_values.count("PASS"),
            "WARNING": result_values.count("WARNING"),
            "FAIL": result_values.count("FAIL"),
        }

        worksheet.write(row_idx, 0, label, formats["normal"])
        worksheet.write(row_idx, 1, len(result_values), formats["normal"])
        worksheet.write(row_idx, 2, counts["PASS"], formats["pass"] if counts["PASS"] else formats["normal"])
        worksheet.write(row_idx, 3, counts["WARNING"], formats["warning"] if counts["WARNING"] else formats["normal"])
        worksheet.write(row_idx, 4, counts["FAIL"], formats["fail"] if counts["FAIL"] else formats["normal"])

    # Overall verdict row
    if "FAIL" in all_results:
        overall = "FAIL"
    elif "WARNING" in all_results:
        overall = "WARNING"
    else:
        overall = "PASS"

    verdict_fmt = formats[_RESULT_FORMAT_KEY[overall]]
    worksheet.write(4, 0, "Overall Verdict", formats["summary_label"])
    worksheet.write(4, 1, overall, verdict_fmt)
    worksheet.set_row(0, 20)


def _write_domain_sheet(
    worksheet: xlsxwriter.worksheet.Worksheet,
    results: list[DomainValidationResult],
    formats: dict,
) -> None:
    worksheet.set_column(0, 0, 18)
    worksheet.set_column(1, 1, 18)
    worksheet.set_column(2, 2, 12)
    worksheet.set_column(3, 3, 28)
    worksheet.set_column(4, 4, 50)

    headers = ["Domain", "Base Domain", "Result", "Error Type", "Reason"]
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, formats["header"])
    worksheet.freeze_panes(1, 0)

    for row_idx, result in enumerate(results, start=1):
        fmt_key = _RESULT_FORMAT_KEY[result.result]
        worksheet.write(row_idx, 0, result.domain, formats["normal"])
        worksheet.write(row_idx, 1, result.base_domain, formats["normal"])
        worksheet.write(row_idx, 2, result.result, formats[fmt_key])
        worksheet.write(row_idx, 3, result.error_type or "", formats["normal"])
        worksheet.write(row_idx, 4, result.reason, formats["normal"])


def _write_suffix_sheet(
    worksheet: xlsxwriter.worksheet.Worksheet,
    results: list[SuffixTypeResult],
    formats: dict,
) -> None:
    worksheet.set_column(0, 0, 28)
    worksheet.set_column(1, 1, 12)
    worksheet.set_column(2, 2, 22)
    worksheet.set_column(3, 3, 14)
    worksheet.set_column(4, 4, 18)
    worksheet.set_column(5, 5, 12)
    worksheet.set_column(6, 6, 16)
    worksheet.set_column(7, 7, 16)
    worksheet.set_column(8, 8, 12)
    worksheet.set_column(9, 9, 28)
    worksheet.set_column(10, 10, 55)

    headers = [
        "Sheet", "Domain", "Item ID", "Layout", "Actual Type", "Actual MAX_LEN",
        "Matched Suffix", "Expected Type", "Result", "Error Type", "Reason",
    ]
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, formats["header"])
    worksheet.freeze_panes(1, 0)

    for row_idx, result in enumerate(results, start=1):
        fmt_key = _RESULT_FORMAT_KEY[result.result]
        worksheet.write(row_idx, 0, result.source_sheet, formats["normal"])
        worksheet.write(row_idx, 1, result.domain, formats["normal"])
        worksheet.write(row_idx, 2, result.item_id, formats["normal"])
        worksheet.write(row_idx, 3, result.layout, formats["normal"])
        worksheet.write(row_idx, 4, result.actual_type, formats["normal"])
        worksheet.write(row_idx, 5, result.actual_max_len if result.actual_max_len is not None else "", formats["normal"])
        worksheet.write(row_idx, 6, result.matched_suffix or "", formats["normal"])
        worksheet.write(row_idx, 7, result.expected_type_category or "", formats["normal"])
        worksheet.write(row_idx, 8, result.result, formats[fmt_key])
        worksheet.write(row_idx, 9, result.error_type or "", formats["normal"])
        worksheet.write(row_idx, 10, result.reason, formats["normal"])


def _write_code_sheet(
    worksheet: xlsxwriter.worksheet.Worksheet,
    results: list[CodeDuplicateResult],
    formats: dict,
) -> None:
    worksheet.set_column(0, 0, 28)
    worksheet.set_column(1, 1, 22)
    worksheet.set_column(2, 2, 50)
    worksheet.set_column(3, 3, 22)
    worksheet.set_column(4, 4, 12)
    worksheet.set_column(5, 5, 24)
    worksheet.set_column(6, 6, 40)

    headers = ["Sheet", "Item ID", "Code", "Duplicate Codes", "Result", "Error Type", "Reason"]
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, formats["header"])
    worksheet.freeze_panes(1, 0)

    for row_idx, result in enumerate(results, start=1):
        fmt_key = _RESULT_FORMAT_KEY[result.result]
        duplicate_str = ", ".join(str(code) for code in result.duplicate_codes) if result.duplicate_codes else ""
        worksheet.write(row_idx, 0, result.source_sheet, formats["normal"])
        worksheet.write(row_idx, 1, result.item_id, formats["normal"])
        worksheet.write(row_idx, 2, result.code_text, formats["normal"])
        worksheet.write(row_idx, 3, duplicate_str, formats["normal"])
        worksheet.write(row_idx, 4, result.result, formats[fmt_key])
        worksheet.write(row_idx, 5, result.error_type or "", formats["normal"])
        worksheet.write(row_idx, 6, result.reason, formats["normal"])


# ---------------------------------------------------------------------------
# Excel reading helper
# ---------------------------------------------------------------------------

def _read_sheet(excel_file: pd.ExcelFile, sheet_name: str, header_row: int) -> pd.DataFrame | None:
    """Read a sheet if it exists, return None otherwise."""
    if sheet_name not in excel_file.sheet_names:
        return None
    return excel_file.parse(sheet_name, header=header_row)


# ---------------------------------------------------------------------------
# Public Entry Point
# ---------------------------------------------------------------------------

def validate_db_specifications(
    input_file: str,
    output_file: str | None = None,
    domain_sheet: str = "Domain Infos",
    spec_sheet: str = "Database Specifications",
    detail_sheet: str = "DB Specifications Table Detail",
    header_row: int = 1,
) -> str:
    """
    Validate a DB Specification Excel file and write an Excel report.

    Returns a message with the path to the generated report.
    """
    input_path = Path(input_file)
    if not input_path.exists():
        return f"File not found: {input_file}"

    if output_file is None:
        output_file = str(input_path.parent / f"{input_path.stem}_validation_report.xlsx")

    excel_file = pd.ExcelFile(input_file)
    available_sheets = excel_file.sheet_names

    # --- Check 1: Domain Validity ---
    domain_df = _read_sheet(excel_file, domain_sheet, header_row)
    if domain_df is None:
        return (
            f"Sheet '{domain_sheet}' not found. "
            f"Available sheets: {available_sheets}"
        )
    domain_results = _validate_domains(domain_df)

    # --- Check 2 + 3: Spec sheets ---
    suffix_results: list[SuffixTypeResult] = []
    code_results: list[CodeDuplicateResult] = []

    for sheet_label in [spec_sheet, detail_sheet]:
        sheet_df = _read_sheet(excel_file, sheet_label, header_row)
        if sheet_df is None:
            continue
        suffix_results.extend(_validate_suffix_types(sheet_df, sheet_label))
        code_results.extend(_validate_code_duplicates(sheet_df, sheet_label))

    if not suffix_results and not code_results:
        return (
            f"Neither '{spec_sheet}' nor '{detail_sheet}' found. "
            f"Available sheets: {available_sheets}"
        )

    # --- Write Report ---
    workbook = xlsxwriter.Workbook(output_file)
    formats = create_formats(workbook)

    summary_ws = workbook.add_worksheet("Summary")
    domain_ws = workbook.add_worksheet("Domain Validation")
    suffix_ws = workbook.add_worksheet("Suffix Type Check")
    code_ws = workbook.add_worksheet("Code Duplicate Check")

    _write_summary_sheet(summary_ws, domain_results, suffix_results, code_results, formats)
    _write_domain_sheet(domain_ws, domain_results, formats)
    _write_suffix_sheet(suffix_ws, suffix_results, formats)
    _write_code_sheet(code_ws, code_results, formats)

    summary_ws.activate()
    workbook.close()

    total_warnings = sum(1 for r in domain_results if r.result == "WARNING") + \
                     sum(1 for r in suffix_results if r.result == "WARNING")
    total_fails = sum(1 for r in domain_results if r.result == "FAIL") + \
                  sum(1 for r in code_results if r.result == "FAIL")

    return (
        f"DB Spec validation completed: {os.path.abspath(output_file)}\n"
        f"  Domain check: {len(domain_results)} entries\n"
        f"  Suffix/Type check: {len(suffix_results)} items\n"
        f"  Code duplicate check: {len(code_results)} items\n"
        f"  WARNING: {total_warnings}  |  FAIL: {total_fails}"
    )
