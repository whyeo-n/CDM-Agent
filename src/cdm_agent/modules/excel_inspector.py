import pandas as pd
import os
from typing import Dict, List


def get_excel_metadata(file_path: str) -> Dict[str, List[str]]:
    """
    Returns a dictionary mapping sheet names to their list of column names.
    Uses pd.ExcelFile to efficiently read headers without loading full datasets.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Excel file not found: {file_path}")

    try:
        # pd.ExcelFile is efficient for multi-sheet inspections
        with pd.ExcelFile(file_path) as excel_file:
            metadata = {}
            for sheet_name in excel_file.sheet_names:
                # Read only the header row (nrows=0) for speed
                df_header = pd.read_excel(excel_file, sheet_name=sheet_name, nrows=0)
                metadata[sheet_name] = list(df_header.columns)
            return metadata
    except Exception as error:
        raise Exception(f"Failed to inspect excel metadata: {str(error)}")
