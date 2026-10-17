import xlsxwriter


# ---------------------------------------------------------------------------
# Color Palette
# ---------------------------------------------------------------------------

COLOR_HEADER_BG = "#F2F2F2"
COLOR_HEADER_DELETED_BG = "#D9D9D9"
COLOR_VALID_BG = "#E8F5E9"
COLOR_VALID_TEXT = "#2E7D32"
COLOR_INVALID_BG = "#FFEBEE"
COLOR_INVALID_TEXT = "#C62828"
COLOR_ERROR_TEXT = "#C62828"
COLOR_WARNING_TEXT = "#E65100"
COLOR_WARNING_BG = "#FFF8E1"
COLOR_INFO_BG = "#E3F2FD"
COLOR_INFO_TEXT = "#1565C0"
COLOR_SKIPPED_BG = "#F5F5F5"
COLOR_SPEC_ERROR_BG = "#FFF9C4"


# ---------------------------------------------------------------------------
# Base Definitions
# ---------------------------------------------------------------------------

DEFAULT_FONT = "Malgun Gothic"
MONO_FONT = "Courier New"
MONO_FONT_SIZE = 10

_BASE_CELL: dict = {"border": 1, "text_wrap": True, "valign": "top", "align": "left", "font_name": DEFAULT_FONT}
_HEADER_BASE: dict = {"border": 1, "valign": "vcenter", "align": "left", "bold": True, "font_name": DEFAULT_FONT}


# ---------------------------------------------------------------------------
# Format Factory
# ---------------------------------------------------------------------------

def create_formats(workbook: xlsxwriter.Workbook) -> dict:
    """
    Creates and returns all standard cell formats for CDM MCP Excel reports.
    Call once per workbook and pass the returned dict to sheet writers.
    """
    return {
        # --- Headers ---
        "header": workbook.add_format({
            **_HEADER_BASE,
            "bg_color": COLOR_HEADER_BG,
        }),
        "header_added": workbook.add_format({
            **_HEADER_BASE,
            "bg_color": COLOR_HEADER_BG,
            "font_color": "red",
        }),
        "header_deleted": workbook.add_format({
            **_HEADER_BASE,
            "bg_color": COLOR_HEADER_DELETED_BG,
            "font_color": "red",
            "font_strikeout": True,
        }),
        "subheader": workbook.add_format({
            **_HEADER_BASE,
            "bg_color": COLOR_HEADER_BG,
        }),

        # --- General Cells ---
        "normal": workbook.add_format(_BASE_CELL),
        "summary_row": workbook.add_format({
            "bold": True,
            "border": 2,
            "text_wrap": True,
            "valign": "top",
            "align": "left",
            "font_name": DEFAULT_FONT,
        }),
        "summary_label": workbook.add_format({"bold": True, "align": "right", "font_name": DEFAULT_FONT}),
        "summary_value": workbook.add_format({"align": "left", "font_name": DEFAULT_FONT}),
        "summary_cell": workbook.add_format({**_BASE_CELL}),
        "summary_title": workbook.add_format({
            **_HEADER_BASE,
            "bg_color": COLOR_HEADER_BG,
            "align": "center_across",
        }),

        # --- Status (DVS) ---
        "valid": workbook.add_format({
            **_BASE_CELL,
            "bg_color": COLOR_VALID_BG,
            "font_color": COLOR_VALID_TEXT,
            "bold": True,
        }),
        "invalid": workbook.add_format({
            **_BASE_CELL,
            "bg_color": COLOR_INVALID_BG,
            "font_color": COLOR_INVALID_TEXT,
            "bold": True,
        }),
        "skipped": workbook.add_format({
            **_BASE_CELL,
            "bg_color": COLOR_SKIPPED_BG,
            "italic": True,
        }),

        # --- Status (DB Spec validation: PASS / WARNING / FAIL) ---
        "pass": workbook.add_format({
            **_BASE_CELL,
            "bg_color": COLOR_VALID_BG,
            "font_color": COLOR_VALID_TEXT,
            "bold": True,
        }),
        "warning": workbook.add_format({
            **_BASE_CELL,
            "bg_color": COLOR_WARNING_BG,
            "font_color": COLOR_WARNING_TEXT,
            "bold": True,
        }),
        "fail": workbook.add_format({
            **_BASE_CELL,
            "bg_color": COLOR_INVALID_BG,
            "font_color": COLOR_INVALID_TEXT,
            "bold": True,
        }),

        # --- DVS Error Detail ---
        "error_code": workbook.add_format({
            **_BASE_CELL,
            "font_color": COLOR_ERROR_TEXT,
            "bold": True,
        }),
        "suggestion": workbook.add_format({
            **_BASE_CELL,
            "font_color": COLOR_INFO_TEXT,
            "italic": True,
        }),
        "corrected": workbook.add_format({
            **_BASE_CELL,
            "bg_color": COLOR_INFO_BG,
            "font_color": COLOR_INFO_TEXT,
            "font_name": MONO_FONT,
            "font_size": MONO_FONT_SIZE,
        }),
        "issue": workbook.add_format({
            **_BASE_CELL,
            "font_color": COLOR_ERROR_TEXT,
            "italic": True,
        }),
        "confidence_low": workbook.add_format({
            **_BASE_CELL,
            "font_color": COLOR_WARNING_TEXT,
        }),

        # --- Specification Cells ---
        "spec": workbook.add_format({
            **_BASE_CELL,
            "font_name": "Courier New",
            "font_size": 9,
        }),
        "spec_error": workbook.add_format({
            **_BASE_CELL,
            "font_name": "Courier New",
            "font_size": 9,
            "bg_color": COLOR_SPEC_ERROR_BG,
        }),

        # --- Excel Compare Change Markers ---
        "added": workbook.add_format({
            **_BASE_CELL,
            "font_color": "red",
        }),
        "deleted": workbook.add_format({
            **_BASE_CELL,
            "font_color": "red",
            "font_strikeout": True,
        }),
        "style_red": workbook.add_format({"color": "red", "font_name": DEFAULT_FONT}),
        "style_red_strikeout": workbook.add_format({"color": "red", "font_strikeout": True, "font_name": DEFAULT_FONT}),
        "style_standard": workbook.add_format({"color": "black", "font_name": DEFAULT_FONT}),
    }
