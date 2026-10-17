import pandas as pd
from datetime import datetime
import os
import shutil
from importlib import resources


def copy_dvs_grammar(output_directory: str) -> str:
    """Copies the bundled DVS Specification Grammar file to the specified output directory."""
    os.makedirs(output_directory, exist_ok=True)

    grammar_filename = "DVS_SPEC_GRAMMAR.md"
    with resources.as_file(
        resources.files("cdm_agent.templates").joinpath(grammar_filename)
    ) as grammar_path:
        destination_path = os.path.join(output_directory, grammar_filename)
        shutil.copy2(grammar_path, destination_path)

    return os.path.abspath(os.path.join(output_directory, grammar_filename))


def copy_timeline_template(output_directory: str) -> str:
    """Copies the bundled DM Timeline template Excel file to the specified output directory."""
    os.makedirs(output_directory, exist_ok=True)

    template_filename = "Timeline for Clinical Data Manager_2026Q1.xlsx"
    with resources.as_file(
        resources.files("cdm_agent.templates").joinpath(template_filename)
    ) as template_path:
        destination_path = os.path.join(output_directory, template_filename)
        shutil.copy2(template_path, destination_path)

    return os.path.abspath(os.path.join(output_directory, template_filename))


def _find_best_timeline_sheet(excel_file: pd.ExcelFile) -> str:
    """Finds the most appropriate sheet name containing Timeline information within the Excel file."""
    sheet_names = excel_file.sheet_names
    target_sheets = [
        sheet for sheet in sheet_names if "Timeline" in sheet or "Schedule" in sheet
    ]
    if target_sheets:
        return target_sheets[0]
    return sheet_names[0]


def convert_dm_timeline_to_csv(input_file: str, output_file: str | None = None) -> str:
    """
    Converts a Timeline Excel file into a CSV format suitable for Google Calendar.
    """
    if not output_file:
        base_name = os.path.splitext(input_file)[0]
        output_file = f"{base_name}.csv"

    with pd.ExcelFile(input_file) as excel_file:
        sheet_name = _find_best_timeline_sheet(excel_file)

        # Extract metadata and headers (e.g., project name)
        dataframe_metadata = excel_file.parse(
            sheet_name, header=None, nrows=2, usecols="B"
        )
        project_name = "Unknown Project"
        if not dataframe_metadata.empty:
            project_name = str(dataframe_metadata.iloc[0, 0]).strip()

        dataframe = excel_file.parse(sheet_name, header=8)

    # Verify required columns
    required_columns = ["Deliverable", "Task", "Start date", "End date"]
    for column in required_columns:
        if column not in dataframe.columns:
            return f"Error: '{column}' column not found in sheet '{sheet_name}'"

    # Filter only valid rows
    dataframe = dataframe.dropna(
        subset=["Deliverable", "Task", "Start date", "End date"], how="all"
    )

    google_calendar_events = []
    skipped_row_count = 0
    for _, row in dataframe.iterrows():
        deliverable = str(row["Deliverable"]).strip()
        task = str(row["Task"]).strip()
        start_date = row["Start date"]
        end_date = row["End date"]

        if pd.isna(start_date) or pd.isna(end_date):
            skipped_row_count += 1
            continue

        # Convert date format
        if isinstance(start_date, datetime):
            formatted_start_date = start_date.strftime("%m/%d/%Y")
        else:
            formatted_start_date = pd.to_datetime(start_date).strftime("%m/%d/%Y")

        if isinstance(end_date, datetime):
            formatted_end_date = end_date.strftime("%m/%d/%Y")
        else:
            formatted_end_date = pd.to_datetime(end_date).strftime("%m/%d/%Y")

        event = {
            "Subject": f"[{project_name}] {deliverable} - {task}",
            "Start Date": formatted_start_date,
            "End Date": formatted_end_date,
            "All Day Event": "True",
            "Description": f"Project: {project_name}\nDeliverable: {deliverable}\nTask: {task}",
        }
        google_calendar_events.append(event)

    if not google_calendar_events:
        return "Error: No valid events found in the timeline."

    dataframe_result = pd.DataFrame(google_calendar_events)
    dataframe_result.to_csv(output_file, index=False, encoding="utf-8-sig")

    result_message = f"Successfully processed {len(google_calendar_events)} events. CSV saved to: {os.path.abspath(output_file)}"
    if skipped_row_count > 0:
        result_message += (
            f" ({skipped_row_count} rows skipped due to missing date values)"
        )
    return result_message
