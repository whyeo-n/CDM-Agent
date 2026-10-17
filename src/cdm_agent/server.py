from mcp.server.fastmcp import FastMCP, Context
import os

# Import modularized engines
from cdm_agent.modules.excel_compare import (
    compare_excel_sheet,
    compare_excel_files_specific,
)
from cdm_agent.modules.calendar_maker import (
    convert_dm_timeline_to_csv,
    copy_dvs_grammar,
    copy_timeline_template,
)
from cdm_agent.modules.dvs_excel_writer import check_dvs_specifications
from cdm_agent.modules.excel_info import get_excel_info
from cdm_agent.modules.dvs_consistency_checker import run_consistency_check
from cdm_agent.modules.pdf_ecrf_compare import compare_blank_ecrf_single_pdf
from cdm_agent.modules.db_spec_validator import validate_db_specifications
from cdm_agent.modules.cdash_reference_generator import generate_cdash_variables as _generate_cdash_variables
from cdm_agent.modules.sop_document_lister import list_signed_documents
from cdm_agent.modules.sop_log_verifier import verify_sop_log_against_index

# Initialize FastMCP server
mcp = FastMCP("cdm")



@mcp.tool()
def compare_common_excel(
    file_before: str,
    file_after: str,
    sheet_name: str,
    header_row: int,
    primary_key_column: list[str],
    output_directory: str | None = None,
) -> str:
    """Compares specific sheets of two Excel files and generates a difference report."""
    try:
        output_filename = (
            os.path.join(output_directory, "comparison_result.xlsx")
            if output_directory
            else "comparison_result.xlsx"
        )
        output_filename = compare_excel_sheet(
            file_before,
            file_after,
            sheet_name,
            header_row,
            primary_key_column,
            output_filename,
        )
        return f"Excel comparison completed: {os.path.abspath(output_filename)}"
    except Exception as error:
        return f"Excel comparison error: {str(error)}"


@mcp.tool()
def compare_db_spec(
    file_before: str,
    file_after: str,
    version: int = 1,
    output_directory: str | None = None,
) -> str:
    """Compares changes in DB Specifications Excel files and generates a report."""
    try:
        from datetime import datetime

        output_filename = (
            os.path.join(
                output_directory,
                f"DB_Spec_Comparison_{datetime.now().strftime('%Y%m%d')}.xlsx",
            )
            if output_directory
            else None
        )
        output_filename = compare_excel_files_specific(
            file_before,
            file_after,
            draft_version=version,
            output_filename=output_filename,
            document_type="DB Spec",
        )
        return f"DB Spec comparison completed: {os.path.abspath(output_filename)}"
    except Exception as error:
        return f"DB Spec comparison error: {str(error)}"



@mcp.tool()
def download_dvs_grammar(output_directory: str) -> str:
    """
    Copies the bundled DVS Specification Grammar reference file (DVS_SPEC_GRAMMAR.md)
    to the specified directory. Use this tool when a user needs the grammar definition
    for writing or reviewing DVS Specifications column entries.
    """
    try:
        saved_path = copy_dvs_grammar(output_directory)
        return f"DVS Specification Grammar saved to: {saved_path}"
    except Exception as error:
        return f"Error downloading DVS grammar: {str(error)}"



@mcp.tool()
def download_timeline_template(output_directory: str) -> str:
    """
    Copies the bundled DM Timeline Excel template to the specified directory.
    Use this tool to obtain the required template before filling in timeline data
    and calling make_google_calendar_csv.
    """
    try:
        saved_path = copy_timeline_template(output_directory)
        return f"Template saved to: {saved_path}"
    except Exception as error:
        return f"Error downloading template: {str(error)}"


@mcp.tool()
def make_google_calendar_csv(input_file: str, output_file: str | None = None) -> str:
    """
    Identifies the valid timeline from a Timeline document and generates a CSV for Google Calendar upload.
    """
    try:
        result_message = convert_dm_timeline_to_csv(input_file, output_file)
        return result_message
    except Exception as error:
        return f"Error occurred: {str(error)}"



@mcp.tool()
def read_excel_info(input_file: str, preview_rows: int = 3) -> str:
    """
    Reads an Excel file and returns its structural information: sheet names,
    column names per sheet, row counts, and a preview of the first few rows.

    Use this tool first when the sheet name, header row, or column names are unknown,
    before calling compare_common_excel, validate_dvs_specifications, or other Excel tools.
    """
    try:
        return get_excel_info(input_file, preview_rows)
    except Exception as error:
        return f"Error reading Excel info: {str(error)}"



@mcp.tool()
def validate_dvs_specifications(
    input_file: str,
    output_file: str | None = None,
    sheet_name: str = "Data Validation Specifications",
    header_row: int = 1,
    column_dvs_id: str = "DVS ID",
    column_dvs_type: str = "DVS Type",
    column_specification: str = "Specification",
) -> str:
    """
    Reads a DVS (Data Validation Specifications) Excel file, validates all Specification
    entries against the DVS grammar, and generates a detailed validation report Excel file.
    Reports syntax errors and auto-corrected suggestions where possible.

    If the sheet or column names differ from the defaults, the tool will return an error
    listing the available options. Re-call with the correct parameter values.
    """
    try:
        return check_dvs_specifications(
            input_file,
            output_file,
            sheet_name,
            header_row,
            column_dvs_id,
            column_dvs_type,
            column_specification,
        )
    except Exception as error:
        return f"DVS validation error: {str(error)}"



@mcp.tool()
async def check_dvs_consistency(
    input_file: str,
    ctx: Context,
    output_file: str | None = None,
    sheet_name: str = "Data Validation Specifications",
    header_row: int = 1,
    column_dvs_id: str = "DVS ID",
    column_dvs_type: str = "DVS Type",
    column_description: str = "Description",
    column_specification: str = "Specification",
) -> str:
    """
    Reads a DVS Excel file and checks whether the natural language Description
    is logically consistent with the formal Specification for each entry.
    Sends entries in batches to the connected LLM client via MCP sampling —
    no separate API key required.

    Recommended: use a lightweight model (e.g., claude-haiku-4-5) for cost efficiency,
    as this tool processes many entries. Results are written to a new Excel report file.
    """
    try:
        return await run_consistency_check(
            input_file,
            ctx,
            output_file,
            sheet_name,
            header_row,
            column_dvs_id,
            column_dvs_type,
            column_description,
            column_specification,
        )
    except Exception as error:
        return f"Consistency check error: {str(error)}"



@mcp.tool()
def compare_ecrf_pdf_single(
    file_before: str,
    file_after: str,
    output_dir: str | None = None,
    output_file: str | None = None,
) -> str:
    """
    Compares two Blank eCRF PDF files and produces a single combined comparison PDF.

    Page assembly rules:
      - Sections with changes  → Before and After pages paired side by side (left = Before, right = After)
      - Unchanged sections     → After pages only (no annotation)
      - Deleted-only sections  → Before pages shown side-by-side with "(Not Exists)" appended at the end

    Annotation colors:
      Red    = Deleted items (Before pages only)
      Green  = Added items   (After pages only)
      Yellow = Modified items (both Before and After pages)

    Output: output_file if specified (absolute path or filename placed in output_dir),
            otherwise {before}_vs_{after}_comparison.pdf in output_dir.
    Input PDFs must be text-based (not scanned).
    """
    try:
        return compare_blank_ecrf_single_pdf(
            file_before, file_after, output_dir, output_file
        )
    except Exception as error:
        return f"eCRF comparison error: {str(error)}"


@mcp.tool()
def validate_db_spec(
    input_file: str,
    output_file: str | None = None,
    domain_sheet: str = "Domain Infos",
    spec_sheet: str = "Database Specifications",
    detail_sheet: str = "DB Specifications Table Detail",
    header_row: int = 1,
) -> str:
    """
    Validates a DB Specification Excel file against CDASH standards (CDASHIG v2.3 / SDTMIG v3.4).
    Runs three checks:
      1. Domain validity — Domain Infos sheet
      2. ITEM ID suffix vs. DB TYPE + MAX_LEN consistency — Database Specifications + Table Detail
      3. CODE field duplicate code numbers — Database Specifications + Table Detail
    Generates an Excel validation report with per-check result sheets and a summary.
    Use read_excel_info first if sheet names differ from defaults.
    """
    try:
        return validate_db_specifications(
            input_file, output_file, domain_sheet, spec_sheet, detail_sheet, header_row
        )
    except Exception as error:
        return f"DB Spec validation error: {str(error)}"


@mcp.tool()
def generate_cdash_variables(
    input_file: str,
    merge: bool = False,
    output_file: str | None = None,
) -> str:
    """
    Generates or updates the CDASH variables JSON reference from a CDASH Model Excel file.
    Use this when a new version of the CDASH Model is released (e.g. CDASH_Model_v1.4.xlsx).

    Args:
        input_file : Path to the CDASH Model Excel file.
        merge      : If True, merges with existing data — new values override conflicts.
                     If False (default), replaces the reference entirely.
        output_file: Output JSON path. Defaults to the built-in package data file.
    """
    try:
        return _generate_cdash_variables(input_file, merge=merge, output_file=output_file)
    except Exception as error:
        return f"CDASH variable generation error: {str(error)}"


@mcp.tool()
def list_dm_documents(
    sop_log_file: str,
    target_folder: str,
    output_file: str | None = None,
) -> str:
    """
    Reads Appendix entries from an SOP Log Excel file and lists the matching signed PDF
    documents found recursively in the target folder.

    For each Appendix entry, searches for PDF files whose name contains the document
    keyword and 'final' or 'signed'. Duplicate document names across versions or
    validity status are merged into a single row.

    Outputs an Excel report with columns: Appendix 종류 / 문서명 / Comment.
    Comment includes warnings for files found inside Working folders or missing signed documents.
    """
    try:
        result_path = list_signed_documents(sop_log_file, target_folder, output_file)
        return f"Document list created: {result_path}"
    except Exception as error:
        return f"list_dm_documents error: {str(error)}"


@mcp.tool()
def verify_sop_log(
    sop_log_file: str,
    sop_index_file: str,
    output_file: str | None = None,
) -> str:
    """
    Compares the SOP Log Excel against the SOP Index PDF and reports version
    and effective date discrepancies for both SOP and Appendix entries.

    Reads the 'Index of SOP' and 'Index of Appendix' sections from the PDF,
    then cross-references each entry recorded in the Excel SOP Log.
    Outputs a two-sheet Excel report (SOP / Appendix) with columns:
    Document No. / Title / Excel Version / Excel Eff. Date / PDF Version / PDF Eff. Date / Result.

    Result values:
      ✓                        — version and date both match
      ⚠ Version mismatch       — version differs
      ⚠ Date mismatch          — effective date differs
      ⚠ Version & Date mismatch — both differ
      ⚠ Not found in PDF       — document number not present in the PDF index
    """
    try:
        result_path = verify_sop_log_against_index(sop_log_file, sop_index_file, output_file)
        return f"Verification complete: {result_path}"
    except Exception as error:
        return f"verify_sop_log error: {str(error)}"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
