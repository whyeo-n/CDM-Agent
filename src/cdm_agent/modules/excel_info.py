import os

import pandas as pd


def get_excel_info(input_file: str, preview_rows: int = 3) -> str:
    """
    Reads an Excel file and returns structural information:
    sheet names, column names per sheet, row counts, and a row preview.

    Use this tool before calling other Excel tools to confirm the correct
    sheet name, header row index, and column names.
    """
    if not os.path.exists(input_file):
        return f"File not found: '{input_file}'"

    file_name = os.path.basename(input_file)
    output_lines = [f"File: {file_name}", ""]

    try:
        with pd.ExcelFile(input_file) as excel_file:
            sheet_names = excel_file.sheet_names
            output_lines.append(f"Sheets ({len(sheet_names)} total): {sheet_names}")
            output_lines.append("")

            for sheet_name in sheet_names:
                output_lines.append(f"{'=' * 60}")
                output_lines.append(f"Sheet: '{sheet_name}'")

                try:
                    # Read without header to inspect raw rows
                    raw_dataframe = excel_file.parse(sheet_name, header=None)
                    total_rows = len(raw_dataframe)
                    total_columns = len(raw_dataframe.columns)
                    output_lines.append(f"Size: {total_rows} rows x {total_columns} columns")
                    output_lines.append("")

                    # Show first N rows as header candidates
                    output_lines.append(f"First {min(preview_rows, total_rows)} rows (header candidates):")
                    for row_index in range(min(preview_rows, total_rows)):
                        row_values = raw_dataframe.iloc[row_index].tolist()
                        formatted_values = [
                            str(value).strip() if str(value) != "nan" else "(empty)"
                            for value in row_values
                        ]
                        output_lines.append(f"  Row {row_index} (header={row_index}): {formatted_values}")

                    output_lines.append("")

                    # Show column names when treating each row as header
                    output_lines.append("Column names by header row:")
                    for header_row_index in range(min(preview_rows, total_rows)):
                        parsed_dataframe = excel_file.parse(sheet_name, header=header_row_index)
                        column_names = [
                            col for col in parsed_dataframe.columns
                            if not str(col).startswith("Unnamed:")
                        ]
                        output_lines.append(f"  header={header_row_index}: {column_names}")

                except Exception as sheet_error:
                    output_lines.append(f"  Could not read sheet: {sheet_error}")

                output_lines.append("")

    except Exception as file_error:
        return f"Could not open file '{file_name}': {file_error}"

    return "\n".join(output_lines)
