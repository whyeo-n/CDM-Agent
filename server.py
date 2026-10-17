from mcp.server.fastmcp import FastMCP
import os

# Import modularized engines
from modules.excel_compare import compare_excel_sheet, compare_excel_files_specific
from modules.calendar_maker import convert_dm_timeline_to_csv
from modules.lab_normal_range_converter import convert_lab_normal_range_excel_to_json
from modules.excel_inspector import get_excel_metadata

# Initialize FastMCP server
mcp = FastMCP("cdm-server")


# --- [Tool 1: Excel Comparison] ---
@mcp.tool()
def compare_common_excel(
    file_before: str,
    file_after: str,
    sheet_name: str,
    header_row: int,
    primary_key_column: list[str],
) -> str:
    """Compares specific sheets of two Excel files and generates a difference report."""
    try:
        output_filename = compare_excel_sheet(
            file_before,
            file_after,
            sheet_name,
            header_row,
            primary_key_column,
            "comparison_result.xlsx",
        )
        return f"Excel comparison completed: {os.path.abspath(output_filename)}"
    except Exception as error:
        return f"Excel comparison error: {str(error)}"


@mcp.tool()
def compare_db_spec(file_before: str, file_after: str, version: int = 1) -> str:
    """Compares changes in DB Specifications Excel files and generates a report."""
    try:
        output_filename = compare_excel_files_specific(
            file_before, file_after, draft_version=version, document_type="DB Spec"
        )
        return f"DB Spec comparison completed: {os.path.abspath(output_filename)}"
    except Exception as error:
        return f"DB Spec comparison error: {str(error)}"


# --- [Tool 2: Google Calendar Integration] ---
@mcp.tool()
def make_google_calendar_csv(input_file: str, output_file: str = None) -> str:
    """
    Identifies the valid timeline from a Timeline document and generates a CSV for Google Calendar upload.
    """
    try:
        result_message = convert_dm_timeline_to_csv(input_file, output_file)
        return result_message
    except Exception as error:
        return f"Error occurred: {str(error)}"


# --- [Tool 3: Lab Normal Range Converter] ---
@mcp.tool()
def convert_lab_normal_range_to_json(
    input_file: str,
    output_file: str = None,
    additional_excluded_sheets: list[str] = None,
) -> str:
    """
    Converts Lab Normal Range (LNR) data from an Excel file into a structured JSON file.
    Args:
        input_file: Path to the input Excel file.
        output_file: Path to the output JSON file.
        additional_excluded_sheets: List of sheet names to exclude from conversion.
    """
    try:
        result_message = convert_lab_normal_range_excel_to_json(
            input_file, output_file, additional_excluded_sheets
        )
        return result_message
    except Exception as error:
        return f"Lab Normal Range conversion error: {str(error)}"


# --- [Tool 4: Excel Inspector] ---
@mcp.tool()
def inspect_excel_metadata(file_path: str) -> dict:
    """
    Inspects an Excel file and returns sheet names and their respective column headers.
    Efficiently reads only the headers using pd.ExcelFile.
    """
    try:
        metadata = get_excel_metadata(file_path)
        return metadata
    except Exception as error:
        return {"error": str(error)}


if __name__ == "__main__":
    mcp.run()
