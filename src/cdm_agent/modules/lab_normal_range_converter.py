import pandas as pd
import json
import os
from typing import Optional


def convert_superscript_units(unit: str) -> str:
    """
    Converts unit strings with 103, 106, 109 to their superscript versions 10³, 10⁶, 10⁹.
    """
    if not isinstance(unit, str):
        return unit

    replacements = {
        "106": "10⁶",
        "103": "10³",
        "109": "10⁹",
    }

    for old_value, new_value in replacements.items():
        unit = unit.replace(old_value, new_value)

    return unit


def convert_lab_row_to_json_object(row: pd.Series) -> dict:
    """
    Transforms a single Excel row from the Lab Normal Range template into a structured JSON object.
    """
    # Convert date data into YYYYMMDD format strings
    effective_date_low_value = (
        pd.to_datetime(row["START DATE"]).strftime("%Y%m%d")
        if pd.notna(row["START DATE"])
        else ""
    )
    effective_date_high_value = (
        pd.to_datetime(row["END DATE"]).strftime("%Y%m%d")
        if pd.notna(row["END DATE"])
        else ""
    )

    # Convert ranges to floating-point numbers
    lower_range_value = (
        float(pd.to_numeric(row["LOWER RANGE"], errors="coerce"))
        if pd.notna(pd.to_numeric(row["LOWER RANGE"], errors="coerce"))
        else ""
    )
    upper_range_value = (
        float(pd.to_numeric(row["UPPER RANGE"], errors="coerce"))
        if pd.notna(pd.to_numeric(row["UPPER RANGE"], errors="coerce"))
        else ""
    )

    # Map operators to JSON keys (gte, gt, lte, lt)
    effective_date_low_key = (
        ""
        if pd.isna(row["START DATE EQUAL"])
        else "gte"
        if row["START DATE EQUAL"] == "≥"
        else "gt"
    )
    effective_date_high_key = (
        ""
        if pd.isna(row["END DATE EQUAL"])
        else "lte"
        if row["END DATE EQUAL"] == "≤"
        else "lt"
    )

    age_low_key = (
        ""
        if pd.isna(row["START AGE EQUAL"])
        else "gte"
        if row["START AGE EQUAL"] == "≥"
        else "gt"
    )
    age_high_key = (
        ""
        if pd.isna(row["END AGE EQUAL"])
        else "lte"
        if row["END AGE EQUAL"] == "≤"
        else "lt"
    )

    range_low_key = (
        ""
        if pd.isna(row["LOWER RANGE EQUAL"])
        else "gte"
        if row["LOWER RANGE EQUAL"] == "≥"
        else "gt"
    )
    range_high_key = (
        ""
        if pd.isna(row["UPPER RANGE EQUAL"])
        else "lte"
        if row["UPPER RANGE EQUAL"] == "≤"
        else "lt"
    )

    unit_value = (
        "NA" if pd.isna(row["UNIT"]) else convert_superscript_units(str(row["UNIT"]))
    )

    # Map age values to integers
    start_age_value = (
        int(pd.to_numeric(row["START AGE"], errors="coerce"))
        if pd.notna(pd.to_numeric(row["START AGE"], errors="coerce"))
        else 0
    )
    end_age_value = (
        int(pd.to_numeric(row["END AGE"], errors="coerce"))
        if pd.notna(pd.to_numeric(row["END AGE"], errors="coerce"))
        else 999
    )

    return {
        "siteCode": row["SITE CODE"],
        "testName": row["TEST NAME"],
        "effectiveDate": {
            effective_date_low_key: effective_date_low_value,
            effective_date_high_key: effective_date_high_value,
        },
        "sex": row["SEX"],
        "age": {
            age_low_key: start_age_value,
            age_high_key: end_age_value,
        },
        "range": {
            range_low_key: lower_range_value,
            range_high_key: upper_range_value,
        },
        "unit": unit_value,
    }


def convert_lab_normal_range_excel_to_json(
    input_file_path: str,
    output_file_path: Optional[str] = None,
    additional_excluded_sheets: Optional[list[str]] = None,
) -> str:
    """
    Reads an Excel file containing Lab Normal Range data and exports it as a JSON file.
    """
    if not os.path.exists(input_file_path):
        raise FileNotFoundError(f"Input file not found: {input_file_path}")

    # Load all sheets from the Excel file
    lab_normal_range_data = pd.read_excel(input_file_path, sheet_name=None, dtype=str)

    # Filter out non-data sheets
    default_excluded_sheets = [
        "Cover",
        "PI Signature",
        "작성 예시",
    ]

    if additional_excluded_sheets:
        default_excluded_sheets.extend(additional_excluded_sheets)

    target_sheet_names = [
        sheet_name
        for sheet_name in lab_normal_range_data.keys()
        if sheet_name not in default_excluded_sheets
    ]

    all_json_objects = []
    for sheet_name in target_sheet_names:
        current_sheet_dataframe = lab_normal_range_data[sheet_name]

        # Convert each row in the sheet to a JSON object
        for _, row in current_sheet_dataframe.iterrows():
            # Skip empty or irrelevant rows (assuming SITE CODE must exist)
            if pd.isna(row["SITE CODE"]) or str(row["SITE CODE"]).strip() == "":
                continue

            try:
                json_object = convert_lab_row_to_json_object(row)
                all_json_objects.append(json_object)
            except Exception as row_error:
                # In a real environment, you might log this error
                print(f"Error processing row in sheet {sheet_name}: {row_error}")
                continue

    # Determine output file path
    if output_file_path is None:
        file_name_without_extension = os.path.splitext(input_file_path)[0]
        output_file_path = f"{file_name_without_extension}.json"

    # Write the list of JSON objects to a file
    with open(output_file_path, "w", encoding="utf-8") as json_file:
        json.dump(all_json_objects, json_file, indent=4, ensure_ascii=False)

    return f"Lab Normal Range data converted successfully: {os.path.abspath(output_file_path)}"
