import pandas as pd
from datetime import datetime
import xlsxwriter
from difflib import SequenceMatcher
import os

from cdm_agent.modules.excel_theme import create_formats


def get_rich_text_segments(
    value_before, value_after, style_standard, style_deleted, style_added
):
    """
    Compares two strings and returns a list of segments for xlsxwriter's write_rich_string.
    """
    matcher = SequenceMatcher(None, str(value_before), str(value_after), autojunk=True)
    rich_segments = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            text = value_before[i1:i2]
            if text:
                rich_segments.extend([style_standard, text])
        elif tag == "delete":
            text = value_before[i1:i2]
            if text:
                rich_segments.extend([style_deleted, text])
        elif tag == "insert":
            text = value_after[j1:j2]
            if text:
                rich_segments.extend([style_added, text])
        elif tag == "replace":
            text_deleted = value_before[i1:i2]
            text_inserted = value_after[j1:j2]
            if text_deleted:
                rich_segments.extend([style_deleted, text_deleted])
            if text_inserted:
                rich_segments.extend([style_added, text_inserted])
    return rich_segments


def _compare_and_write_sheet(
    workbook,
    sheet_name,
    df_before,
    df_after,
    primary_key_columns,
    difference_threshold=100,
):
    """
    Compares two DataFrames and writes the results to a worksheet within the given workbook.
    """
    worksheet = workbook.add_worksheet(sheet_name)

    # --- Data Preparation ---
    primary_keys = (
        [primary_key_columns]
        if isinstance(primary_key_columns, str)
        else list(primary_key_columns)
    )

    # Ensure no NaN values and convert all to string for comparison
    df_before = df_before.fillna("").astype(str)
    df_after = df_after.fillna("").astype(str)

    columns_before = list(df_before.columns)
    columns_after = list(df_after.columns)
    added_columns = [col for col in columns_after if col not in columns_before]
    deleted_columns = [col for col in columns_before if col not in columns_after]

    # Maintain column order from 'before' file, with added columns at the end
    all_display_columns = columns_before + added_columns

    try:
        keys_before = (
            df_before.set_index(primary_keys).index.tolist()
            if not df_before.empty
            else []
        )
        keys_after = (
            df_after.set_index(primary_keys).index.tolist()
            if not df_after.empty
            else []
        )
        df_before = df_before.set_index(primary_keys, drop=False)
        df_after = df_after.set_index(primary_keys, drop=False)
    except KeyError as e:
        raise KeyError(
            f"Primary key column '{e.args[0]}' not found in one of the sheets."
        ) from e

    final_row_order = keys_before.copy()
    keys_before_set = set(keys_before)
    last_anchor_key = None
    for key in keys_after:
        if key not in keys_before_set:
            if last_anchor_key is None:
                final_row_order.insert(0, key)
            else:
                try:
                    anchor_index = final_row_order.index(last_anchor_key)
                    final_row_order.insert(anchor_index + 1, key)
                except ValueError:
                    final_row_order.append(key)
        last_anchor_key = key

    # --- Format Definitions ---
    formats = create_formats(workbook)

    # --- Header and Column Widths ---
    display_columns_with_summary = all_display_columns + ["Change Summary"]
    for col_idx, col_name in enumerate(display_columns_with_summary):
        # Set column width
        if col_name == "Change Summary":
            worksheet.set_column(col_idx, col_idx, 30)
        else:
            len_b = (
                df_before[col_name].str.len().max()
                if col_name in columns_before and not df_before.empty
                else 0
            )
            len_a = (
                df_after[col_name].str.len().max()
                if col_name in columns_after and not df_after.empty
                else 0
            )
            max_len = max(len_b, len_a, len(str(col_name)))
            width = min(max(max_len * 1.2, 15), 60)
            worksheet.set_column(col_idx, col_idx, width)

        # Write header
        header_format = formats["header"]
        if col_name in added_columns:
            header_format = formats["header_added"]
        if col_name in deleted_columns:
            header_format = formats["header_deleted"]
        worksheet.write(0, col_idx, col_name, header_format)

    # --- Summary Row ---
    summary_message = (
        f"Columns: {len(added_columns)} Added, {len(deleted_columns)} Deleted"
    )
    if added_columns:
        summary_message += f"\nAdded: {', '.join(added_columns)}"
    if deleted_columns:
        summary_message += f"\nDeleted: {', '.join(deleted_columns)}"

    worksheet.write(1, 0, "*Summary*", formats["summary_row"])
    worksheet.write(
        1,
        len(display_columns_with_summary) - 1,
        summary_message,
        formats["summary_row"],
    )
    for col_idx in range(1, len(display_columns_with_summary) - 1):
        worksheet.write(1, col_idx, "*", formats["summary_row"])

    # --- Data Rows ---
    for row_idx, row_key in enumerate(final_row_order, 2):
        is_in_before = row_key in df_before.index
        is_in_after = row_key in df_after.index
        row_before = df_before.loc[[row_key]].iloc[0] if is_in_before else None
        row_after = df_after.loc[[row_key]].iloc[0] if is_in_after else None

        row_status = ""
        if not is_in_after:
            row_status = "Row Deleted"
        elif not is_in_before:
            row_status = "Row Added"
        else:
            is_modified = any(
                str(row_before.get(col, "")) != str(row_after.get(col, ""))
                for col in all_display_columns
                if col not in primary_keys
            )
            row_status = "Row Modified" if is_modified else "No Change"

        for col_idx, col_name in enumerate(display_columns_with_summary):
            if col_name == "Change Summary":
                worksheet.write(row_idx, col_idx, row_status, formats["normal"])
                continue

            val_before = (
                row_before[col_name]
                if row_before is not None and col_name in row_before
                else ""
            )
            val_after = (
                row_after[col_name]
                if row_after is not None and col_name in row_after
                else ""
            )

            if row_status == "Row Deleted":
                worksheet.write(row_idx, col_idx, val_before, formats["deleted"])
            elif row_status == "Row Added":
                worksheet.write(row_idx, col_idx, val_after, formats["added"])
            elif col_name in primary_keys or val_before == val_after:
                worksheet.write(row_idx, col_idx, val_after, formats["normal"])
            else:  # Modified cell
                placeholder = "[EMPTY]"
                is_long_string = (
                    len(val_before) > difference_threshold
                    or len(val_after) > difference_threshold
                )

                if not val_before and val_after:  # Value created
                    worksheet.write(
                        row_idx,
                        col_idx,
                        f"{placeholder} -> {val_after}",
                        formats["added"],
                    )
                elif val_before and not val_after:  # Value deleted
                    worksheet.write(
                        row_idx,
                        col_idx,
                        f"{val_before} -> {placeholder}",
                        formats["added"],
                    )
                elif is_long_string:  # Modified, but too long for arrow
                    segments = get_rich_text_segments(
                        val_before,
                        val_after,
                        formats["style_standard"],
                        formats["style_red_strikeout"],
                        formats["style_red"],
                    )
                    try:
                        worksheet.write_rich_string(
                            row_idx, col_idx, *segments, formats["normal"]
                        )
                    except Exception:  # Fallback
                        worksheet.write(
                            row_idx,
                            col_idx,
                            f"{val_before} -> {val_after}",
                            formats["added"],
                        )
                else:  # Modified, short enough for arrow
                    worksheet.write(
                        row_idx,
                        col_idx,
                        f"{val_before} -> {val_after}",
                        formats["added"],
                    )


def compare_excel_sheet(
    file_before,
    file_after,
    sheet_name,
    header_row,
    primary_key_columns,
    output_filename,
    difference_threshold=100,
):
    """
    Compares a single sheet from two Excel files and generates a detailed report.
    """
    try:
        df_before = pd.read_excel(file_before, sheet_name=sheet_name, header=header_row, dtype=str, keep_default_na=False)
    except Exception as e:
        raise ValueError(
            f"Could not read sheet '{sheet_name}' from file '{os.path.basename(file_before)}'. Error: {e}"
        )

    try:
        df_after = pd.read_excel(file_after, sheet_name=sheet_name, header=header_row, dtype=str, keep_default_na=False)
    except Exception as e:
        raise ValueError(
            f"Could not read sheet '{sheet_name}' from file '{os.path.basename(file_after)}'. Error: {e}"
        )

    workbook = xlsxwriter.Workbook(output_filename)
    _compare_and_write_sheet(
        workbook,
        sheet_name,
        df_before,
        df_after,
        primary_key_columns,
        difference_threshold,
    )
    workbook.close()
    return output_filename


def compare_excel_files_specific(
    file_before, file_after, draft_version=1, output_filename=None, document_type=None
):
    """
    Compares specific sheets from two Excel files based on a document type (e.g., 'DB Spec').
    """
    if document_type != "DB Spec":
        raise ValueError(f"UNSUPPORTED_DOCUMENT_TYPE: {document_type}")

    HEADER_ROW_INDEX = 1
    TARGET_CONFIG = [
        {"sheet": "Database Specifications", "pks": ["DOMAIN", "ITEM ID"]},
        {
            "sheet": "DB Specifications Table Detail",
            "pks": ["DOMAIN", "ITEM ID", "ITEM SEQ"],
        },
        {"sheet": "Domain Infos", "pks": ["DOMAIN"]},
        {"sheet": "Visit Infos", "pks": ["VISIT NAME"]},
    ]

    if not output_filename:
        output_filename = f"DB_Spec_Comparison_{datetime.now().strftime('%Y%m%d')}.xlsx"

    with pd.ExcelFile(file_before) as excel_before, pd.ExcelFile(file_after) as excel_after:
        workbook = xlsxwriter.Workbook(output_filename)
        processed_sheets = 0

        try:
            for item in TARGET_CONFIG:
                sheet_name = item["sheet"]
                pk_cols = item["pks"]

                if sheet_name not in excel_after.sheet_names:
                    continue

                df_after = excel_after.parse(sheet_name, header=HEADER_ROW_INDEX, dtype=str, keep_default_na=False)

                if sheet_name not in excel_before.sheet_names:
                    # Handle sheet that exists only in 'after' file
                    worksheet = workbook.add_worksheet(sheet_name)
                    worksheet.write(
                        0, 0, "This sheet is new and was not found in the 'before' file."
                    )
                    processed_sheets += 1
                    continue

                df_before = excel_before.parse(sheet_name, header=HEADER_ROW_INDEX, dtype=str, keep_default_na=False)

                # PK Existence check
                if not all(c in df_before.columns for c in pk_cols) or not all(
                    c in df_after.columns for c in pk_cols
                ):
                    worksheet = workbook.add_worksheet(sheet_name)
                    worksheet.write(
                        0, 0, f"ERROR: Primary key columns {pk_cols} not found in both sheets."
                    )
                    continue

                _compare_and_write_sheet(workbook, sheet_name, df_before, df_after, pk_cols)
                processed_sheets += 1

            if processed_sheets == 0:
                raise ValueError("No matching target sheets found for comparison.")
        finally:
            workbook.close()

    return output_filename
