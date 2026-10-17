import os
import re
import unicodedata
from dataclasses import dataclass, field
from collections import defaultdict

import fitz  # PyMuPDF
import pdfplumber


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLOR_DELETED = (0.95, 0.3, 0.3)    # Red
COLOR_ADDED = (0.3, 0.75, 0.3)      # Green
COLOR_MODIFIED = (1.0, 0.8, 0.1)    # Yellow
COLOR_FILL_ALPHA = 0.25


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class ECRFItem:
    """A single input field within an eCRF section."""
    item_label: str
    item_value: str
    page_index: int        # 0-based physical page index
    bbox: tuple | None     # pdfplumber (x0, top, x1, bottom)


@dataclass
class ECRFSection:
    """A named group of items within a page, bounded by section header rows.
    May span multiple physical pages if content overflows (continuation pages).
    """
    section_name: str      # "" if the page has no explicit section headers
    items: list[ECRFItem] = field(default_factory=list)
    page_indices: list[int] = field(default_factory=list)
    header_bboxes: dict[int, tuple] = field(default_factory=dict)   # page_index → header row bbox
    content_bboxes: dict[int, tuple] = field(default_factory=dict)  # page_index → union of item bboxes


@dataclass
class ECRFPage:
    """A logical eCRF page, which may span multiple physical PDF pages (continuation)."""
    visit: str
    page_name: str
    page_indices: list[int] = field(default_factory=list)
    sections: list[ECRFSection] = field(default_factory=list)


@dataclass
class PageDiff:
    """A logical page that is entirely Added or Deleted."""
    visit: str
    page_name: str
    change_type: str            # "Added" or "Deleted"
    page_indices_before: list[int] = field(default_factory=list)
    page_indices_after: list[int] = field(default_factory=list)


@dataclass
class SectionDiff:
    """A section that is entirely Added or Deleted within a matching page."""
    visit: str
    page_name: str
    section_name: str
    change_type: str            # "Added" or "Deleted"
    page_indices_before: list[int] = field(default_factory=list)
    page_indices_after: list[int] = field(default_factory=list)
    bboxes_before: dict[int, tuple] = field(default_factory=dict)  # page_index → combined bbox
    bboxes_after: dict[int, tuple] = field(default_factory=dict)


@dataclass
class ItemDiff:
    """A field-level change within a matching section."""
    visit: str
    page_name: str
    section_name: str
    item_label: str
    value_before: str
    value_after: str
    change_type: str            # "Added", "Deleted", or "Modified"
    page_indices_before: list[int] = field(default_factory=list)
    page_indices_after: list[int] = field(default_factory=list)
    bboxes_before: list[tuple] = field(default_factory=list)
    bboxes_after: list[tuple] = field(default_factory=list)


@dataclass
class SpecialPageCell:
    """A single cell on a special (non-eCRF-structured) page.

    For flat key-value pages (e.g., Cover): col_key is "".
    For matrix pages (e.g., Schedule of Events): col_key is the visit/column header.
    """
    row_key: str
    col_key: str
    cell_value: str
    page_index: int
    bbox: tuple | None


@dataclass
class SpecialPage:
    """A special page (e.g., Cover, Schedule of Events) without eCRF Visit/Page/Section structure.

    Consecutive physical pages that form one logical table (horizontal continuation) are merged
    into a single SpecialPage.
    """
    page_identifier: str
    page_indices: list[int] = field(default_factory=list)
    cells: list[SpecialPageCell] = field(default_factory=list)


@dataclass
class SpecialCellDiff:
    """A cell-level change on a special (non-eCRF-structured) page."""
    page_identifier: str
    row_key: str
    col_key: str
    value_before: str
    value_after: str
    change_type: str  # "Added", "Deleted", or "Modified"
    page_indices_before: list[int] = field(default_factory=list)
    page_indices_after: list[int] = field(default_factory=list)
    bboxes_before: list[tuple] = field(default_factory=list)
    bboxes_after: list[tuple] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Text Utilities
# ---------------------------------------------------------------------------

def _combine_bboxes(bboxes: list[tuple]) -> tuple:
    """Returns the smallest bounding box that contains all given bboxes."""
    return (
        min(b[0] for b in bboxes),
        min(b[1] for b in bboxes),
        max(b[2] for b in bboxes),
        max(b[3] for b in bboxes),
    )


def _normalize_text(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text))
    return re.sub(r'\s+', ' ', normalized).strip()


def _extract_item_value(row: list) -> str:
    """Extracts the item value from a table row (first non-empty cell after col[0])."""
    for cell in row[1:]:
        normalized = _normalize_text(cell)
        if normalized:
            return normalized
    return ""


def _is_title_only_row(row: list) -> bool:
    """Returns True if the row has content only in col[0] (all other cells empty or None)."""
    return all(_normalize_text(cell) == "" for cell in row[1:])


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _extract_row_bboxes(table_obj) -> list[tuple | None]:
    """Returns a list of (x0, top, x1, bottom) for each row in a pdfplumber Table object."""
    row_bboxes = []
    for table_row in table_obj.rows:
        valid_cells = [cell for cell in table_row.cells if cell is not None]
        if not valid_cells:
            row_bboxes.append(None)
            continue
        x0 = min(cell[0] for cell in valid_cells)
        top = min(cell[1] for cell in valid_cells)
        x1 = max(cell[2] for cell in valid_cells)
        bottom = max(cell[3] for cell in valid_cells)
        row_bboxes.append((x0, top, x1, bottom))
    return row_bboxes


def _is_bold_row(page, row_bbox: tuple) -> bool:
    """Returns True if the majority of visible characters in the row use a bold font.

    Section headers in eCRF PDFs are consistently styled with bold text.
    Description / informational overflow rows use regular (non-bold) weight.
    Uses pdfplumber's page.chars for per-character font information.
    Overlap-based detection: a character is included if its bbox overlaps with row_bbox.
    """
    x0, top, x1, bottom = row_bbox
    row_chars = [
        char for char in page.chars
        if char.get('x0', 0) < x1 + 2
        and char.get('x1', 0) > x0 - 2
        and char.get('top', 0) < bottom + 2
        and char.get('bottom', 0) > top - 2
        and char.get('text', '').strip()
    ]
    if not row_chars:
        return False
    bold_count = sum(
        1 for char in row_chars
        if 'Bold' in char.get('fontname', '') or 'bold' in char.get('fontname', '')
    )
    return bold_count > len(row_chars) * 0.5


def _extract_page_structure(page, page_index: int) -> ECRFPage | None:
    """Extracts the logical section/item structure from a single PDF page.

    Page structure assumption:
      - table[0]: page header (row 0 = visit name, row 1 = page name)
      - table[1+]: content tables

    Content table classification (applied to each table after table[0]):
      - If first row is title-only (all cols after col[0] empty):
          - Matches page_name → page-level header repetition; skip first row, start unnamed section
          - Otherwise → section table; first row is the section name header
      - If first row is NOT title-only → sub-table (dropdown options for a preceding field)

    Within a section table, non-first title-only rows are further classified by font weight:
      - Bold → start of a new named sub-section
      - Not bold → informational/description row (skip; no grouping change)
    """
    tables = page.extract_tables()
    if len(tables) < 2:
        return None

    header_table = tables[0]
    if len(header_table) < 2:
        return None

    visit = _normalize_text(header_table[0][0] if header_table[0] else "")
    page_name = _normalize_text(header_table[1][0] if len(header_table) > 1 else "")

    if not visit or not page_name:
        return None

    # eCRF page header tables contain a visit-name column plus several form-metadata columns
    # (Protocol, Site, Screening No., Name, Sign), giving a width of 4 or more.
    # Narrow tables (width <= 3) are flat key-value layouts such as the Cover page, which must
    # be routed to the SpecialPage pipeline instead.
    header_row_0 = header_table[0] if header_table else []
    if len(header_row_0) <= 3:
        return None

    table_objects = page.find_tables()

    ecrf_page = ECRFPage(visit=visit, page_name=page_name, page_indices=[page_index])

    # Extract rows 2+ of the header table as items in a hidden metadata section.
    # These rows hold cover-page fields (Version, Date, Document Status, etc.) that appear
    # inside table[0] rather than in a separate content table.
    if len(header_table) > 2:
        header_table_obj = table_objects[0] if table_objects else None
        header_row_bboxes = (
            _extract_row_bboxes(header_table_obj) if header_table_obj else [None] * len(header_table)
        )
        header_metadata_items = []
        for row_index in range(2, len(header_table)):
            row = header_table[row_index]
            if not row or not row[0]:
                continue
            label = _normalize_text(row[0])
            if not label:
                continue
            row_bbox = header_row_bboxes[row_index] if row_index < len(header_row_bboxes) else None
            header_metadata_items.append(ECRFItem(
                item_label=label,
                item_value=_extract_item_value(row),
                page_index=page_index,
                bbox=row_bbox,
            ))
        if header_metadata_items:
            ecrf_page.sections.append(ECRFSection(
                section_name="[Header Metadata]",
                items=header_metadata_items,
                page_indices=[page_index],
            ))

    # Maps item_label → index in ecrf_page.sections so sub-tables can locate the owning section.
    item_section_map: dict[str, int] = {}
    current_section_index: int = -1

    def _get_or_create_section(name: str) -> int:
        """Returns index of an existing section with this name, or appends a new one."""
        for index, existing_section in enumerate(ecrf_page.sections):
            if existing_section.section_name == name:
                return index
        ecrf_page.sections.append(
            ECRFSection(section_name=name, page_indices=[page_index])
        )
        return len(ecrf_page.sections) - 1

    for table_index in range(1, len(tables)):
        table_data = tables[table_index]
        if not table_data:
            continue

        table_obj = table_objects[table_index] if table_index < len(table_objects) else None
        row_bboxes = _extract_row_bboxes(table_obj) if table_obj else [None] * len(table_data)

        first_row = table_data[0]
        if not first_row or not first_row[0]:
            continue
        first_label = _normalize_text(first_row[0])
        if not first_label:
            continue

        # A table whose first row is NOT title-only is a sub-table (dropdown options)
        # ONLY IF a named section has already been established. Otherwise, it is the
        # first content table on the page and has no explicit section header — treat it
        # as an unnamed section so its fields are not discarded.
        if not _is_title_only_row(first_row) and current_section_index >= 0:
            # Sub-table: dropdown options that supplement fields from a preceding section.
            # Override existing item values if this table provides a richer (longer) value.
            for row_index, row in enumerate(table_data):
                if not row or not row[0]:
                    continue
                label = _normalize_text(row[0])
                if not label:
                    continue
                value = _extract_item_value(row)
                row_bbox = row_bboxes[row_index] if row_index < len(row_bboxes) else None

                existing_section_idx = item_section_map.get(label, -1)
                if existing_section_idx >= 0:
                    target_section = ecrf_page.sections[existing_section_idx]
                    for existing_item in target_section.items:
                        if existing_item.item_label == label:
                            if len(value) > len(existing_item.item_value):
                                existing_item.item_value = value
                                if row_bbox is not None:
                                    existing_item.bbox = row_bbox
                            break
                else:
                    # New item not seen in any section yet — add to current section
                    current_section = ecrf_page.sections[current_section_index]
                    if label not in {item.item_label for item in current_section.items}:
                        current_section.items.append(ECRFItem(
                            item_label=label,
                            item_value=value,
                            page_index=page_index,
                            bbox=row_bbox,
                        ))
                        item_section_map[label] = current_section_index
            continue

        # Content table: either a named section (title-only first row) or an unnamed
        # section (first row is a field row with no preceding section header yet).
        if _is_title_only_row(first_row):
            if first_label == page_name:
                # Page-level section name repetition; begin an unnamed section.
                section_name = ""
            else:
                section_name = first_label
            content_start_row = 1  # First row is the section header; fields start at row 1
        else:
            # No section header — all rows in this table are field rows.
            section_name = ""
            content_start_row = 0

        current_section_index = _get_or_create_section(section_name)
        current_section = ecrf_page.sections[current_section_index]

        # Record the header row bbox for any section whose first content table row was
        # skipped (either a named section header or a page-name repetition row).
        if content_start_row == 1:
            first_row_bbox = row_bboxes[0] if row_bboxes else None
            if first_row_bbox is not None:
                current_section.header_bboxes.setdefault(page_index, first_row_bbox)

        # Process content rows of this section table.
        item_bboxes_on_page: list[tuple] = []

        for row_index in range(content_start_row, len(table_data)):
            row = table_data[row_index]
            if not row or not row[0]:
                continue
            label = _normalize_text(row[0])
            if not label:
                continue
            row_bbox = row_bboxes[row_index] if row_index < len(row_bboxes) else None

            if _is_title_only_row(row):
                # Non-first title-only row: bold → new sub-section; not bold → description.
                if row_bbox is not None and _is_bold_row(page, row_bbox):
                    # Save accumulated bboxes for the current section before switching.
                    if item_bboxes_on_page:
                        current_section.content_bboxes[page_index] = _combine_bboxes(item_bboxes_on_page)
                    # Start a new section.
                    current_section_index = _get_or_create_section(label)
                    current_section = ecrf_page.sections[current_section_index]
                    current_section.header_bboxes.setdefault(page_index, row_bbox)
                    item_bboxes_on_page = []
                # else: description / informational row — skip without changing grouping
                continue

            # Regular field row.
            value = _extract_item_value(row)
            if label not in {item.item_label for item in current_section.items}:
                current_section.items.append(ECRFItem(
                    item_label=label,
                    item_value=value,
                    page_index=page_index,
                    bbox=row_bbox,
                ))
                item_section_map[label] = current_section_index

            if row_bbox is not None:
                item_bboxes_on_page.append(row_bbox)

        # Save accumulated item bboxes for the last active section in this table.
        if item_bboxes_on_page:
            current_section.content_bboxes[page_index] = _combine_bboxes(item_bboxes_on_page)

    # Discard sections with no items (e.g., empty continuation placeholders).
    ecrf_page.sections = [sec for sec in ecrf_page.sections if sec.items]

    return ecrf_page if ecrf_page.sections else None


def extract_ecrf_structure(pdf_path: str) -> dict[tuple[str, str], ECRFPage]:
    """Extracts the full hierarchical structure from a Blank eCRF PDF.

    Returns a dict keyed by (visit, page_name) → ECRFPage.

    Continuation pages (same visit+page_name across multiple physical pages) are merged:
      - Sections with matching names have items merged (first occurrence of each label wins).
      - Sections appearing for the first time on a continuation page are appended.
    """
    pages_by_key: dict[tuple[str, str], ECRFPage] = {}

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            extracted = _extract_page_structure(page, page_index)
            if extracted is None:
                continue

            key = (extracted.visit, extracted.page_name)

            if key not in pages_by_key:
                pages_by_key[key] = extracted
            else:
                # Continuation page — merge into the existing ECRFPage.
                existing_page = pages_by_key[key]
                if page_index not in existing_page.page_indices:
                    existing_page.page_indices.append(page_index)

                existing_section_map = {
                    sec.section_name: sec for sec in existing_page.sections
                }

                for new_section in extracted.sections:
                    if new_section.section_name in existing_section_map:
                        target = existing_section_map[new_section.section_name]
                        # Merge items (first occurrence of each label wins).
                        existing_labels = {item.item_label for item in target.items}
                        for item in new_section.items:
                            if item.item_label not in existing_labels:
                                target.items.append(item)
                                existing_labels.add(item.item_label)
                        # Merge page-level bbox records.
                        for pg_idx, bbox in new_section.header_bboxes.items():
                            target.header_bboxes.setdefault(pg_idx, bbox)
                        for pg_idx, bbox in new_section.content_bboxes.items():
                            target.content_bboxes.setdefault(pg_idx, bbox)
                        if page_index not in target.page_indices:
                            target.page_indices.append(page_index)
                    else:
                        # New section encountered on a continuation page.
                        existing_page.sections.append(new_section)
                        existing_section_map[new_section.section_name] = new_section

    return pages_by_key


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _section_combined_bboxes(section: ECRFSection) -> dict[int, tuple]:
    """Returns per-page combined bbox covering both the header row and all item rows."""
    result: dict[int, tuple] = {}
    all_page_indices = set(section.header_bboxes) | set(section.content_bboxes)
    for page_idx in all_page_indices:
        parts: list[tuple] = []
        if page_idx in section.header_bboxes:
            parts.append(section.header_bboxes[page_idx])
        if page_idx in section.content_bboxes:
            parts.append(section.content_bboxes[page_idx])
        if parts:
            result[page_idx] = _combine_bboxes(parts)
    return result


def _compare_items(
    section_before: ECRFSection,
    section_after: ECRFSection,
    visit: str,
    page_name: str,
    section_name: str,
) -> list[ItemDiff]:
    """Compares items within two matching sections and returns field-level diffs."""
    before_map = {item.item_label: item for item in section_before.items}
    after_map = {item.item_label: item for item in section_after.items}

    all_labels: list[str] = []
    seen: set[str] = set()
    for item in section_before.items:
        if item.item_label not in seen:
            all_labels.append(item.item_label)
            seen.add(item.item_label)
    for item in section_after.items:
        if item.item_label not in seen:
            all_labels.append(item.item_label)
            seen.add(item.item_label)

    diffs: list[ItemDiff] = []
    for label in all_labels:
        before_item = before_map.get(label)
        after_item = after_map.get(label)

        if before_item and not after_item:
            change_type = "Deleted"
            value_before, value_after = before_item.item_value, ""
            pages_before = [before_item.page_index]
            pages_after = []
            bboxes_before = [before_item.bbox] if before_item.bbox else []
            bboxes_after = []
        elif not before_item and after_item:
            change_type = "Added"
            value_before, value_after = "", after_item.item_value
            pages_before = []
            pages_after = [after_item.page_index]
            bboxes_before = []
            bboxes_after = [after_item.bbox] if after_item.bbox else []
        elif before_item and after_item:
            if before_item.item_value == after_item.item_value:
                continue  # Unchanged — omit from diff
            change_type = "Modified"
            value_before, value_after = before_item.item_value, after_item.item_value
            pages_before = [before_item.page_index]
            pages_after = [after_item.page_index]
            bboxes_before = [before_item.bbox] if before_item.bbox else []
            bboxes_after = [after_item.bbox] if after_item.bbox else []
        else:
            continue

        diffs.append(ItemDiff(
            visit=visit,
            page_name=page_name,
            section_name=section_name,
            item_label=label,
            value_before=value_before,
            value_after=value_after,
            change_type=change_type,
            page_indices_before=pages_before,
            page_indices_after=pages_after,
            bboxes_before=bboxes_before,
            bboxes_after=bboxes_after,
        ))

    return diffs


def compare_ecrf_structures(
    before: dict[tuple[str, str], ECRFPage],
    after: dict[tuple[str, str], ECRFPage],
) -> tuple[list[PageDiff], list[SectionDiff], list[ItemDiff]]:
    """Hierarchical comparison: Page → Section → Item.

    Returns:
      - page_diffs:    Pages entirely Added or Deleted
      - section_diffs: Sections entirely Added or Deleted within matching pages
      - item_diffs:    Field-level Added / Deleted / Modified within matching sections
    """
    page_diffs: list[PageDiff] = []
    section_diffs: list[SectionDiff] = []
    item_diffs: list[ItemDiff] = []

    all_keys: list[tuple[str, str]] = []
    seen_keys: set[tuple[str, str]] = set()
    for key in before:
        if key not in seen_keys:
            all_keys.append(key)
            seen_keys.add(key)
    for key in after:
        if key not in seen_keys:
            all_keys.append(key)
            seen_keys.add(key)

    for key in all_keys:
        visit, page_name = key
        page_before = before.get(key)
        page_after = after.get(key)

        if page_before and not page_after:
            page_diffs.append(PageDiff(
                visit=visit, page_name=page_name, change_type="Deleted",
                page_indices_before=page_before.page_indices, page_indices_after=[],
            ))
            continue

        if not page_before and page_after:
            page_diffs.append(PageDiff(
                visit=visit, page_name=page_name, change_type="Added",
                page_indices_before=[], page_indices_after=page_after.page_indices,
            ))
            continue

        # Page exists in both — compare sections.
        before_section_map = {sec.section_name: sec for sec in page_before.sections}
        after_section_map = {sec.section_name: sec for sec in page_after.sections}

        all_section_names: list[str] = []
        seen_sections: set[str] = set()
        for sec in page_before.sections:
            if sec.section_name not in seen_sections:
                all_section_names.append(sec.section_name)
                seen_sections.add(sec.section_name)
        for sec in page_after.sections:
            if sec.section_name not in seen_sections:
                all_section_names.append(sec.section_name)
                seen_sections.add(sec.section_name)

        for section_name in all_section_names:
            sec_before = before_section_map.get(section_name)
            sec_after = after_section_map.get(section_name)

            if sec_before and not sec_after:
                section_diffs.append(SectionDiff(
                    visit=visit, page_name=page_name, section_name=section_name,
                    change_type="Deleted",
                    page_indices_before=sec_before.page_indices, page_indices_after=[],
                    bboxes_before=_section_combined_bboxes(sec_before), bboxes_after={},
                ))
            elif not sec_before and sec_after:
                section_diffs.append(SectionDiff(
                    visit=visit, page_name=page_name, section_name=section_name,
                    change_type="Added",
                    page_indices_before=[], page_indices_after=sec_after.page_indices,
                    bboxes_before={}, bboxes_after=_section_combined_bboxes(sec_after),
                ))
            elif sec_before and sec_after:
                item_diffs.extend(
                    _compare_items(sec_before, sec_after, visit, page_name, section_name)
                )

    return page_diffs, section_diffs, item_diffs


def _generate_section_diffs_for_page_diffs(
    page_diffs: list[PageDiff],
    section_diffs: list[SectionDiff],
    structure_before: dict[tuple[str, str], ECRFPage],
    structure_after: dict[tuple[str, str], ECRFPage],
    empty_after_pages: dict[tuple[str, str], list[int]],
) -> list[SectionDiff]:
    """Supplements section_diffs with section-level annotations for entirely Added/Deleted pages.

    For Deleted pages: generates SectionDiff(Deleted) for every section in the Before page,
    so that red block annotations are drawn on the Before side even when the After side shows
    "(Not Exists)" or an empty structure page.

    For Added pages: generates SectionDiff(Added) for every section in the After page,
    so that green block annotations are drawn on the After side.

    The original PageDiff entries are kept unchanged — this function only appends to section_diffs.
    """
    additional_section_diffs: list[SectionDiff] = []

    for page_diff in page_diffs:
        key = (page_diff.visit, page_diff.page_name)

        if page_diff.change_type == "Deleted":
            page_before = structure_before.get(key)
            if page_before:
                after_empty_indices = empty_after_pages.get(key, [])
                for section in page_before.sections:
                    additional_section_diffs.append(SectionDiff(
                        visit=page_diff.visit,
                        page_name=page_diff.page_name,
                        section_name=section.section_name,
                        change_type="Deleted",
                        page_indices_before=list(section.page_indices),
                        page_indices_after=list(after_empty_indices),
                        bboxes_before=_section_combined_bboxes(section),
                        bboxes_after={},
                    ))

        elif page_diff.change_type == "Added":
            page_after = structure_after.get(key)
            if page_after:
                for section in page_after.sections:
                    additional_section_diffs.append(SectionDiff(
                        visit=page_diff.visit,
                        page_name=page_diff.page_name,
                        section_name=section.section_name,
                        change_type="Added",
                        page_indices_before=[],
                        page_indices_after=list(section.page_indices),
                        bboxes_before={},
                        bboxes_after=_section_combined_bboxes(section),
                    ))

    return section_diffs + additional_section_diffs


# ---------------------------------------------------------------------------
# Special Page Extraction (Cover, Schedule of Events, etc.)
# ---------------------------------------------------------------------------

def _extract_page_identifier(page) -> str:
    """Returns the best-guess title for a non-eCRF page.

    Checks (in order):
    1. Title-only first row in the first table (stable across versions).
    2. For matrix tables, a fingerprint derived from the first few assessment row keys
       (invariant to column additions/deletions, so it remains consistent across versions).
    3. First meaningful text line extracted from the full page (last resort).
    """
    tables_data = page.extract_tables()

    if tables_data and tables_data[0] and tables_data[0][0]:
        first_row = tables_data[0][0]
        if _is_title_only_row(first_row):
            title = _normalize_text(first_row[0])
            if title and len(title) >= 3:
                return title

    # For matrix tables (e.g., Schedule of Events): use first few assessment row-header
    # values as fingerprint — invariant to column additions/deletions.
    # For flat key-value tables (e.g., Cover): use first row label, not value — invariant
    # to value changes between versions.
    if tables_data:
        for table in tables_data:
            if _is_matrix_table(table):
                header_idx = _find_matrix_header_row(table)
                if header_idx >= 0:
                    row_keys = [
                        _normalize_text(row[0])
                        for row in table[header_idx + 1: header_idx + 4]
                        if row and row[0] and _normalize_text(row[0])
                    ]
                    if row_keys:
                        return "matrix:" + "|".join(row_keys)
            else:
                for row in table[:3]:
                    if row and row[0]:
                        first_label = _normalize_text(row[0])
                        if first_label and len(first_label) >= 3:
                            return "flat:" + first_label

    page_text = page.extract_text() or ""
    for line in page_text.strip().split('\n'):
        cleaned_line = _normalize_text(line)
        if cleaned_line and len(cleaned_line) >= 3:
            return cleaned_line

    return ""


def _is_matrix_table(table_data: list[list]) -> bool:
    """Returns True if the table has matrix structure (column headers with empty first cell).

    Detects a header row where col[0] is empty but other columns contain text — the pattern
    used by Schedule of Events tables.
    """
    for row in table_data[:5]:
        if not row:
            continue
        first_cell = _normalize_text(row[0]) if row[0] is not None else ""
        if not first_cell and any(_normalize_text(cell) for cell in row[1:] if cell is not None):
            return True
    return False


def _extract_cells_from_flat_table(
    table_data: list[list],
    table_obj,
    page_index: int,
) -> list[SpecialPageCell]:
    """Extracts cells from a flat key-value table (e.g., Cover page).

    Treats col[0] as row_key and the first non-empty remaining cell as the value.
    col_key is always "" for flat tables.
    """
    row_bboxes = _extract_row_bboxes(table_obj) if table_obj else [None] * len(table_data)
    cells: list[SpecialPageCell] = []
    for row_index, row in enumerate(table_data):
        if not row or not row[0]:
            continue
        row_key = _normalize_text(row[0])
        if not row_key:
            continue
        row_bbox = row_bboxes[row_index] if row_index < len(row_bboxes) else None
        value = _extract_item_value(row)
        cells.append(SpecialPageCell(
            row_key=row_key, col_key="",
            cell_value=value,
            page_index=page_index,
            bbox=row_bbox,
        ))
    return cells


def _get_cell_bbox(table_obj, row_index: int, col_index: int) -> tuple | None:
    """Returns the pdfplumber bbox for a specific cell in a table, or None if unavailable."""
    if table_obj is None:
        return None
    try:
        return table_obj.rows[row_index].cells[col_index]
    except (IndexError, AttributeError):
        return None


def _extract_cells_from_matrix_table(
    table_data: list[list],
    table_obj,
    page_index: int,
    col_headers: list[str],
    header_row_index: int,
) -> list[SpecialPageCell]:
    """Extracts cells from a matrix table (e.g., Schedule of Events).

    Includes column header cells (row_key="") so that column additions and deletions
    are detected as SpecialCellDiff entries. Uses per-cell bboxes from the pdfplumber
    Table object to avoid annotation overlap across cells in the same row.

    col_headers: column header labels extracted from table_data[header_row_index][1:].
    header_row_index: the table row index of the header row.
    """
    cells: list[SpecialPageCell] = []

    # Header cells (row_key="" marks the header row).
    for col_offset, col_key in enumerate(col_headers):
        if not col_key:
            continue
        actual_col_index = col_offset + 1
        cell_bbox = _get_cell_bbox(table_obj, header_row_index, actual_col_index)
        cells.append(SpecialPageCell(
            row_key="", col_key=col_key,
            cell_value=col_key,
            page_index=page_index,
            bbox=cell_bbox,
        ))

    # Data cells.
    for row_index in range(header_row_index + 1, len(table_data)):
        row = table_data[row_index]
        if not row or not row[0]:
            continue
        row_key = _normalize_text(row[0])
        if not row_key:
            continue
        for col_offset in range(len(col_headers)):
            col_key = col_headers[col_offset]
            if not col_key:
                continue
            actual_col_index = col_offset + 1
            value = (
                _normalize_text(row[actual_col_index])
                if actual_col_index < len(row) and row[actual_col_index] is not None
                else ""
            )
            cell_bbox = _get_cell_bbox(table_obj, row_index, actual_col_index)
            cells.append(SpecialPageCell(
                row_key=row_key, col_key=col_key,
                cell_value=value,
                page_index=page_index,
                bbox=cell_bbox,
            ))
    return cells


def _extract_nontable_text_blocks(
    page,
    table_objects: list,
    page_index: int,
    y_gap_threshold: float = 30.0,
) -> list[SpecialPageCell]:
    """Extracts text blocks that lie outside all table bboxes on the page.

    Words are clustered by vertical proximity (gaps larger than y_gap_threshold start a new
    block). Each cluster becomes a SpecialPageCell whose row_key is derived from the cluster's
    top Y position (rounded to the nearest 50 pt) to stay stable across PDF versions.
    """
    table_bboxes = [table_obj.bbox for table_obj in table_objects]

    words = page.extract_words()
    non_table_words = [
        word for word in words
        if not any(
            word['x0'] >= bbox[0] - 2 and word['x1'] <= bbox[2] + 2
            and word['top'] >= bbox[1] - 2 and word['bottom'] <= bbox[3] + 2
            for bbox in table_bboxes
        )
    ]
    if not non_table_words:
        return []

    # Sort by vertical position and cluster words with small gaps into blocks.
    non_table_words.sort(key=lambda word: word['top'])
    clusters: list[list[dict]] = [[non_table_words[0]]]
    for word in non_table_words[1:]:
        prev_bottom = max(w['bottom'] for w in clusters[-1])
        if word['top'] - prev_bottom > y_gap_threshold:
            clusters.append([word])
        else:
            clusters[-1].append(word)

    cells: list[SpecialPageCell] = []
    for cluster in clusters:
        # Sort words within the cluster top-to-bottom, then left-to-right.
        cluster_sorted = sorted(cluster, key=lambda w: (round(w['top'] / 5) * 5, w['x0']))
        cluster_text = _normalize_text(' '.join(w['text'] for w in cluster_sorted))
        if not cluster_text:
            continue
        cluster_top = min(w['top'] for w in cluster)
        row_key = f"[text_block:{int(cluster_top // 50)}]"
        x0 = min(w['x0'] for w in cluster)
        x1 = max(w['x1'] for w in cluster)
        top = cluster_top
        bottom = max(w['bottom'] for w in cluster)
        cells.append(SpecialPageCell(
            row_key=row_key,
            col_key="",
            cell_value=cluster_text,
            page_index=page_index,
            bbox=(x0, top, x1, bottom),
        ))
    return cells


def _is_horizontal_continuation(pdf, prev_page_number: int, curr_page_number: int) -> bool:
    """Returns True if curr_page_number is a horizontal continuation of prev_page_number.

    Horizontal continuation: same assessment rows (col[0]), different visit columns (row[0]).
    Both pages must be matrix-type (first cell of header row empty) and share at least 50%
    of their row headers.
    """
    prev_tables = pdf.pages[prev_page_number].extract_tables()
    curr_tables = pdf.pages[curr_page_number].extract_tables()

    if not prev_tables or not curr_tables:
        return False

    prev_table = prev_tables[0]
    curr_table = curr_tables[0]

    if not _is_matrix_table(prev_table) or not _is_matrix_table(curr_table):
        return False

    prev_row_headers = {
        _normalize_text(row[0]) for row in prev_table
        if row and row[0] and _normalize_text(row[0])
        and not all(_normalize_text(c) == "" or c is None for c in row[1:])
    }
    curr_row_headers = {
        _normalize_text(row[0]) for row in curr_table
        if row and row[0] and _normalize_text(row[0])
        and not all(_normalize_text(c) == "" or c is None for c in row[1:])
    }

    if not prev_row_headers or not curr_row_headers:
        return False

    overlap = prev_row_headers & curr_row_headers
    return len(overlap) >= len(curr_row_headers) * 0.5


def _find_matrix_header_row(table_data: list[list]) -> int:
    """Returns the index of the first matrix-style header row (first cell empty).

    Returns -1 if no such row is found.
    """
    for row_index, row in enumerate(table_data):
        if not row:
            continue
        first_cell = _normalize_text(row[0]) if row[0] is not None else ""
        if not first_cell and any(_normalize_text(cell) for cell in row[1:] if cell is not None):
            return row_index
    return -1


def _extract_special_page_group(pdf, page_indices: list[int]) -> SpecialPage | None:
    """Extracts a SpecialPage from one or more physical pages forming a single logical special page.

    Multi-page groups are treated as horizontal continuations of a matrix table:
    the same row headers appear on each page, with different column headers per page.
    Cells are merged by (row_key, col_key) across all pages.
    """
    if not page_indices:
        return None

    first_page = pdf.pages[page_indices[0]]

    # Skip pages that have an eCRF-style form header (first-table first-row with 4+ non-empty
    # cells: visit name plus form-metadata column labels like Protocol, Site, Screening No.).
    # These are empty eCRF continuation or deleted pages whose physical shell remains and are
    # already handled by _find_empty_section_pages — they should not appear as special pages.
    first_page_tables = first_page.extract_tables()
    if first_page_tables and first_page_tables[0] and first_page_tables[0][0]:
        non_empty_first_row = [
            c for c in first_page_tables[0][0] if c and _normalize_text(c)
        ]
        if len(non_empty_first_row) >= 4:
            return None

    page_identifier = _extract_page_identifier(first_page)
    if not page_identifier:
        page_identifier = f"Page {page_indices[0] + 1}"

    # Single-page extraction
    if len(page_indices) == 1:
        pn = page_indices[0]
        page = pdf.pages[pn]
        tables_data = page.extract_tables()
        tables_obj = page.find_tables()

        if not tables_data:
            return None

        main_table_idx = max(range(len(tables_data)), key=lambda i: len(tables_data[i]))
        table_data = tables_data[main_table_idx]
        table_obj = tables_obj[main_table_idx] if main_table_idx < len(tables_obj) else None

        if _is_matrix_table(table_data):
            header_row_index = _find_matrix_header_row(table_data)
            if header_row_index < 0:
                return None
            col_headers = [
                _normalize_text(cell) for cell in table_data[header_row_index][1:]
                if cell is not None
            ]
            cells = _extract_cells_from_matrix_table(
                table_data, table_obj, pn, col_headers, header_row_index
            )
        else:
            # Flat key-value page (e.g., Cover): extract from ALL non-matrix tables so that
            # tables like "Sponsor" that are separate from the main table are not missed.
            cells = []
            seen_row_keys: set[str] = set()
            for table_index, flat_table_data in enumerate(tables_data):
                if _is_matrix_table(flat_table_data):
                    continue
                flat_table_obj = tables_obj[table_index] if table_index < len(tables_obj) else None
                for cell in _extract_cells_from_flat_table(flat_table_data, flat_table_obj, pn):
                    if cell.row_key not in seen_row_keys:
                        cells.append(cell)
                        seen_row_keys.add(cell.row_key)
            # Also extract text blocks that lie outside all tables (e.g., page header with
            # version number and document title printed above the main table area).
            cells.extend(_extract_nontable_text_blocks(page, tables_obj, pn))

        if not cells:
            return None

        return SpecialPage(
            page_identifier=page_identifier,
            page_indices=[pn],
            cells=cells,
        )

    # Multi-page: horizontal continuation — merge cells by (row_key, col_key).
    # row_key="" is reserved for column header cells.
    # First occurrence of each (row_key, col_key) pair wins.
    merged_cells: dict[tuple[str, str], tuple[str, int, tuple | None]] = {}
    row_order: list[str] = []    # preserve assessment row order from first page
    col_order: list[str] = []    # preserve column header order across all pages

    for pn in page_indices:
        page = pdf.pages[pn]
        tables_data = page.extract_tables()
        tables_obj = page.find_tables()

        if not tables_data:
            continue

        main_table_idx = max(range(len(tables_data)), key=lambda i: len(tables_data[i]))
        table_data = tables_data[main_table_idx]
        table_obj = tables_obj[main_table_idx] if main_table_idx < len(tables_obj) else None

        header_row_index = _find_matrix_header_row(table_data)
        if header_row_index < 0:
            continue

        col_headers = [
            _normalize_text(cell) for cell in table_data[header_row_index][1:]
            if cell is not None
        ]

        # Header cells for this page's columns.
        for col_offset, col_key in enumerate(col_headers):
            if not col_key:
                continue
            cell_key = ("", col_key)
            cell_bbox = _get_cell_bbox(table_obj, header_row_index, col_offset + 1)
            if cell_key not in merged_cells:
                merged_cells[cell_key] = (col_key, pn, cell_bbox)
                col_order.append(col_key)

        # Data cells.
        for row_index in range(header_row_index + 1, len(table_data)):
            row = table_data[row_index]
            if not row or not row[0]:
                continue
            row_key = _normalize_text(row[0])
            if not row_key:
                continue

            if row_key not in {rk for rk, _ in merged_cells if rk}:
                row_order.append(row_key)

            for col_offset, col_key in enumerate(col_headers):
                if not col_key:
                    continue
                actual_col_index = col_offset + 1
                cell_key = (row_key, col_key)
                if cell_key not in merged_cells:
                    value = (
                        _normalize_text(row[actual_col_index])
                        if actual_col_index < len(row) and row[actual_col_index] is not None
                        else ""
                    )
                    cell_bbox = _get_cell_bbox(table_obj, row_index, actual_col_index)
                    merged_cells[cell_key] = (value, pn, cell_bbox)

    cells = []
    # Header cells first (in col_order).
    for col_key in col_order:
        cell_key = ("", col_key)
        if cell_key in merged_cells:
            value, pn, bbox = merged_cells[cell_key]
            cells.append(SpecialPageCell(
                row_key="", col_key=col_key,
                cell_value=value, page_index=pn, bbox=bbox,
            ))
    # Data cells (in row_order × col_order).
    for row_key in row_order:
        for col_key in col_order:
            cell_key = (row_key, col_key)
            if cell_key in merged_cells:
                value, pn, bbox = merged_cells[cell_key]
                cells.append(SpecialPageCell(
                    row_key=row_key, col_key=col_key,
                    cell_value=value, page_index=pn, bbox=bbox,
                ))

    if not cells:
        return None

    return SpecialPage(
        page_identifier=page_identifier,
        page_indices=page_indices,
        cells=cells,
    )


def extract_special_pages(
    pdf_path: str,
    ecrf_page_indices: set[int],
) -> dict[str, SpecialPage]:
    """Extracts special pages (non-eCRF-structured) from a PDF.

    Pages not covered by the eCRF extraction (Cover, Schedule of Events, etc.) are grouped
    and parsed into SpecialPage objects keyed by their page_identifier.

    Consecutive pages that form a horizontal continuation (same row headers, different column
    headers) are merged into a single SpecialPage.
    """
    result: dict[str, SpecialPage] = {}

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        unmapped_indices = [
            pn for pn in range(total_pages) if pn not in ecrf_page_indices
        ]
        if not unmapped_indices:
            return {}

        # Group consecutive pages; check for horizontal continuation within each consecutive run.
        groups: list[list[int]] = []
        for pn in unmapped_indices:
            if not groups or pn != groups[-1][-1] + 1:
                groups.append([pn])
            elif _is_horizontal_continuation(pdf, groups[-1][-1], pn):
                groups[-1].append(pn)
            else:
                groups.append([pn])

        for group in groups:
            special_page = _extract_special_page_group(pdf, group)
            if special_page:
                result[special_page.page_identifier] = special_page

    return result


def compare_special_pages(
    before_special: dict[str, SpecialPage],
    after_special: dict[str, SpecialPage],
) -> list[SpecialCellDiff]:
    """Compares special pages by page_identifier, returning cell-level diffs.

    Pages present in only one version (entirely added or deleted) are skipped —
    they are shown side-by-side with '(Not Exists)' without cell-level annotation.
    """
    diffs: list[SpecialCellDiff] = []

    # Special pages that exist only in Before → all cells Deleted.
    for identifier in set(before_special) - set(after_special):
        for cell in before_special[identifier].cells:
            diffs.append(SpecialCellDiff(
                page_identifier=identifier,
                row_key=cell.row_key, col_key=cell.col_key,
                value_before=cell.cell_value, value_after="",
                change_type="Deleted",
                page_indices_before=[cell.page_index],
                page_indices_after=[],
                bboxes_before=[cell.bbox] if cell.bbox else [],
                bboxes_after=[],
            ))

    # Special pages that exist only in After → all cells Added.
    for identifier in set(after_special) - set(before_special):
        for cell in after_special[identifier].cells:
            diffs.append(SpecialCellDiff(
                page_identifier=identifier,
                row_key=cell.row_key, col_key=cell.col_key,
                value_before="", value_after=cell.cell_value,
                change_type="Added",
                page_indices_before=[],
                page_indices_after=[cell.page_index],
                bboxes_before=[],
                bboxes_after=[cell.bbox] if cell.bbox else [],
            ))

    # Process identifiers that exist in both versions.
    common_identifiers = set(before_special) & set(after_special)
    for identifier in common_identifiers:
        page_before = before_special[identifier]
        page_after = after_special[identifier]

        # Collect unique row_keys in order (excluding "" which is the column header sentinel).
        before_data_row_keys: list[str] = []
        seen_before_rows: set[str] = set()
        for cell in page_before.cells:
            if cell.row_key and cell.row_key not in seen_before_rows:
                before_data_row_keys.append(cell.row_key)
                seen_before_rows.add(cell.row_key)

        after_data_row_keys: list[str] = []
        seen_after_rows: set[str] = set()
        for cell in page_after.cells:
            if cell.row_key and cell.row_key not in seen_after_rows:
                after_data_row_keys.append(cell.row_key)
                seen_after_rows.add(cell.row_key)

        before_row_key_set = set(before_data_row_keys)
        after_row_key_set = set(after_data_row_keys)

        # --- Column header cells (row_key == "") ---
        # Added column header → "Added" (rendered blue in _annotate_document).
        # Deleted column header → "Deleted" (red).
        # Value changed → "Modified" (yellow).
        before_col_headers = {
            cell.col_key: cell
            for cell in page_before.cells if cell.row_key == ""
        }
        after_col_headers = {
            cell.col_key: cell
            for cell in page_after.cells if cell.row_key == ""
        }
        all_col_keys: list[str] = []
        seen_col_keys: set[str] = set()
        for cell in page_before.cells:
            if cell.row_key == "" and cell.col_key not in seen_col_keys:
                all_col_keys.append(cell.col_key)
                seen_col_keys.add(cell.col_key)
        for cell in page_after.cells:
            if cell.row_key == "" and cell.col_key not in seen_col_keys:
                all_col_keys.append(cell.col_key)
                seen_col_keys.add(cell.col_key)

        for col_key in all_col_keys:
            header_before = before_col_headers.get(col_key)
            header_after = after_col_headers.get(col_key)
            if header_before and not header_after:
                diffs.append(SpecialCellDiff(
                    page_identifier=identifier,
                    row_key="", col_key=col_key,
                    value_before=header_before.cell_value, value_after="",
                    change_type="Deleted",
                    page_indices_before=[header_before.page_index],
                    page_indices_after=[],
                    bboxes_before=[header_before.bbox] if header_before.bbox else [],
                    bboxes_after=[],
                ))
            elif not header_before and header_after:
                # "Added" with row_key=="" signals a new column — rendered blue.
                diffs.append(SpecialCellDiff(
                    page_identifier=identifier,
                    row_key="", col_key=col_key,
                    value_before="", value_after=header_after.cell_value,
                    change_type="Added",
                    page_indices_before=[],
                    page_indices_after=[header_after.page_index],
                    bboxes_before=[],
                    bboxes_after=[header_after.bbox] if header_after.bbox else [],
                ))
            elif header_before and header_after:
                if header_before.cell_value != header_after.cell_value:
                    diffs.append(SpecialCellDiff(
                        page_identifier=identifier,
                        row_key="", col_key=col_key,
                        value_before=header_before.cell_value,
                        value_after=header_after.cell_value,
                        change_type="Modified",
                        page_indices_before=[header_before.page_index],
                        page_indices_after=[header_after.page_index],
                        bboxes_before=[header_before.bbox] if header_before.bbox else [],
                        bboxes_after=[header_after.bbox] if header_after.bbox else [],
                    ))

        # --- Data rows (row_key != "") ---
        # Deleted row → one diff per row with merged bbox covering all cells in the row.
        # Added row   → one diff per row with merged bbox covering all cells in the row.
        # Modified row (row exists in both but cells differ, including new-column cells) →
        #   individual cell diffs with change_type="Modified".
        all_data_row_keys: list[str] = []
        seen_all_rows: set[str] = set()
        for row_key in before_data_row_keys:
            if row_key not in seen_all_rows:
                all_data_row_keys.append(row_key)
                seen_all_rows.add(row_key)
        for row_key in after_data_row_keys:
            if row_key not in seen_all_rows:
                all_data_row_keys.append(row_key)
                seen_all_rows.add(row_key)

        for row_key in all_data_row_keys:
            row_exists_before = row_key in before_row_key_set
            row_exists_after = row_key in after_row_key_set

            if row_exists_before and not row_exists_after:
                # Entire row deleted — merge all cell bboxes into one row-spanning bbox.
                row_cells_before = [
                    cell for cell in page_before.cells if cell.row_key == row_key
                ]
                row_bboxes_before = [c.bbox for c in row_cells_before if c.bbox]
                merged_bbox_before = _combine_bboxes(row_bboxes_before) if row_bboxes_before else None
                page_index_before = row_cells_before[0].page_index if row_cells_before else 0
                diffs.append(SpecialCellDiff(
                    page_identifier=identifier,
                    row_key=row_key, col_key="",
                    value_before=row_key, value_after="",
                    change_type="Deleted",
                    page_indices_before=[page_index_before],
                    page_indices_after=[],
                    bboxes_before=[merged_bbox_before] if merged_bbox_before else [],
                    bboxes_after=[],
                ))

            elif not row_exists_before and row_exists_after:
                # Entire row added — merge all cell bboxes into one row-spanning bbox.
                row_cells_after = [
                    cell for cell in page_after.cells if cell.row_key == row_key
                ]
                row_bboxes_after = [c.bbox for c in row_cells_after if c.bbox]
                merged_bbox_after = _combine_bboxes(row_bboxes_after) if row_bboxes_after else None
                page_index_after = row_cells_after[0].page_index if row_cells_after else 0
                diffs.append(SpecialCellDiff(
                    page_identifier=identifier,
                    row_key=row_key, col_key="",
                    value_before="", value_after=row_key,
                    change_type="Added",
                    page_indices_before=[],
                    page_indices_after=[page_index_after],
                    bboxes_before=[],
                    bboxes_after=[merged_bbox_after] if merged_bbox_after else [],
                ))

            else:
                # Row exists in both — compare cell by cell.
                # Cells in a new column (col_key in After but not Before) are treated as
                # Modified rather than Added, because the row itself still exists.
                # Similarly, cells in a deleted column become Modified.
                row_cells_before_map = {
                    cell.col_key: cell
                    for cell in page_before.cells if cell.row_key == row_key
                }
                row_cells_after_map = {
                    cell.col_key: cell
                    for cell in page_after.cells if cell.row_key == row_key
                }
                all_cell_col_keys: list[str] = []
                seen_cell_cols: set[str] = set()
                for cell in page_before.cells:
                    if cell.row_key == row_key and cell.col_key not in seen_cell_cols:
                        all_cell_col_keys.append(cell.col_key)
                        seen_cell_cols.add(cell.col_key)
                for cell in page_after.cells:
                    if cell.row_key == row_key and cell.col_key not in seen_cell_cols:
                        all_cell_col_keys.append(cell.col_key)
                        seen_cell_cols.add(cell.col_key)

                for col_key in all_cell_col_keys:
                    cell_before = row_cells_before_map.get(col_key)
                    cell_after = row_cells_after_map.get(col_key)

                    if cell_before and not cell_after:
                        # Column deleted from an existing row → Modified.
                        # Only annotate when the cell had a meaningful (non-empty) value.
                        if not cell_before.cell_value:
                            continue
                        diffs.append(SpecialCellDiff(
                            page_identifier=identifier,
                            row_key=row_key, col_key=col_key,
                            value_before=cell_before.cell_value, value_after="",
                            change_type="Modified",
                            page_indices_before=[cell_before.page_index],
                            page_indices_after=[],
                            bboxes_before=[cell_before.bbox] if cell_before.bbox else [],
                            bboxes_after=[],
                        ))
                    elif not cell_before and cell_after:
                        # New column added to an existing row → Modified.
                        # Only annotate when the new cell has a meaningful (non-empty) value.
                        if not cell_after.cell_value:
                            continue
                        diffs.append(SpecialCellDiff(
                            page_identifier=identifier,
                            row_key=row_key, col_key=col_key,
                            value_before="", value_after=cell_after.cell_value,
                            change_type="Modified",
                            page_indices_before=[],
                            page_indices_after=[cell_after.page_index],
                            bboxes_before=[],
                            bboxes_after=[cell_after.bbox] if cell_after.bbox else [],
                        ))
                    elif cell_before and cell_after:
                        if cell_before.cell_value == cell_after.cell_value:
                            continue
                        diffs.append(SpecialCellDiff(
                            page_identifier=identifier,
                            row_key=row_key, col_key=col_key,
                            value_before=cell_before.cell_value,
                            value_after=cell_after.cell_value,
                            change_type="Modified",
                            page_indices_before=[cell_before.page_index],
                            page_indices_after=[cell_after.page_index],
                            bboxes_before=[cell_before.bbox] if cell_before.bbox else [],
                            bboxes_after=[cell_after.bbox] if cell_after.bbox else [],
                        ))

    return diffs


# ---------------------------------------------------------------------------
# Change Detection
# ---------------------------------------------------------------------------

def _diffs_to_annotation_targets(
    section_diffs: list[SectionDiff],
    item_diffs: list[ItemDiff],
    special_cell_diffs: list[SpecialCellDiff],
    side: str,  # "before" or "after"
) -> list[tuple[int, tuple, tuple]]:
    """Converts structured diffs into (page_index, bbox, color_rgb) drawing instructions.

    Handles all diff types (section, item, special cell) and applies vertical bbox
    merging before returning. The caller is responsible for bounds-checking page_index
    against the target document.
    """
    color_map = {
        "Deleted": COLOR_DELETED,
        "Added": COLOR_ADDED,
        "Modified": COLOR_MODIFIED,
    }
    raw_targets: list[tuple[int, tuple, tuple]] = []

    for section_diff in section_diffs:
        color = color_map[section_diff.change_type]
        bboxes = section_diff.bboxes_before if side == "before" else section_diff.bboxes_after
        for page_index, bbox in bboxes.items():
            raw_targets.append((page_index, bbox, color))

    for item_diff in item_diffs:
        color = color_map[item_diff.change_type]
        if side == "before":
            page_bbox_pairs = list(zip(item_diff.page_indices_before, item_diff.bboxes_before))
        else:
            page_bbox_pairs = list(zip(item_diff.page_indices_after, item_diff.bboxes_after))
        for page_index, bbox in page_bbox_pairs:
            if bbox is not None:
                raw_targets.append((page_index, bbox, color))

    for special_cell_diff in special_cell_diffs:
        color = color_map[special_cell_diff.change_type]
        if side == "before":
            page_bbox_pairs = list(zip(
                special_cell_diff.page_indices_before, special_cell_diff.bboxes_before
            ))
        else:
            page_bbox_pairs = list(zip(
                special_cell_diff.page_indices_after, special_cell_diff.bboxes_after
            ))
        for page_index, bbox in page_bbox_pairs:
            if bbox is not None:
                raw_targets.append((page_index, bbox, color))

    return _merge_adjacent_bboxes(raw_targets)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _pdfplumber_bbox_to_fitz(bbox: tuple) -> fitz.Rect:
    """Converts pdfplumber bbox (x0, top, x1, bottom) to PyMuPDF Rect."""
    x0, top, x1, bottom = bbox
    return fitz.Rect(x0, top, x1, bottom)


def _annotate_rect(fitz_page: fitz.Page, rect: fitz.Rect, color: tuple) -> None:
    """Draws a filled, bordered rectangle annotation on the page."""
    shape = fitz_page.new_shape()
    shape.draw_rect(rect)
    shape.finish(
        color=color,
        fill=color,
        fill_opacity=COLOR_FILL_ALPHA,
        width=1.2,
    )
    shape.commit()


def _set_pdf_scroll_layout(doc: fitz.Document) -> None:
    """Configures the PDF to open in continuous single-column scroll mode."""
    catalog_xref = doc.pdf_catalog()
    doc.xref_set_key(catalog_xref, "PageLayout", "/OneColumn")


def _merge_adjacent_bboxes(
    annotated_items: list[tuple[int, tuple, tuple]],
    gap_threshold: float = 3.0,
) -> list[tuple[int, tuple, tuple]]:
    """Merges vertically adjacent bboxes that share the same page, color, and X range.

    Two bboxes are merged only when both conditions hold:
      1. Vertical gap (next_top - current_bottom) <= gap_threshold
      2. X ranges overlap (their horizontal spans intersect)

    The X-range check ensures that bboxes from different table columns are never merged
    into a wide rectangle that would visually cover unchanged cells between them.

    Each item is a (page_index, bbox, color_rgb) tuple.
    """
    grouped: dict[tuple, list[tuple]] = defaultdict(list)
    for page_index, bbox, color in annotated_items:
        grouped[(page_index, color)].append(bbox)

    result: list[tuple[int, tuple, tuple]] = []
    for (page_index, color), bboxes in grouped.items():
        sorted_bboxes = sorted(bboxes, key=lambda bbox: bbox[1])  # sort by top coordinate
        merged = [sorted_bboxes[0]]
        for bbox in sorted_bboxes[1:]:
            last = merged[-1]
            vertical_gap = bbox[1] - last[3]
            x_overlap = min(last[2], bbox[2]) - max(last[0], bbox[0])
            if vertical_gap <= gap_threshold and x_overlap > 0:
                merged[-1] = (
                    min(last[0], bbox[0]),
                    min(last[1], bbox[1]),
                    max(last[2], bbox[2]),
                    max(last[3], bbox[3]),
                )
            else:
                merged.append(bbox)
        for bbox in merged:
            result.append((page_index, bbox, color))
    return result


def _annotate_document(
    doc: fitz.Document,
    annotation_targets: list[tuple[int, tuple, tuple]],
) -> None:
    """Draws pre-computed annotation targets onto a fitz.Document in place."""
    for page_index, bbox, color in annotation_targets:
        if page_index < len(doc):
            _annotate_rect(doc[page_index], _pdfplumber_bbox_to_fitz(bbox), color)


def _annotate_pdf(
    source_pdf_path: str,
    output_pdf_path: str,
    section_diffs: list[SectionDiff],
    item_diffs: list[ItemDiff],
    special_cell_diffs: list[SpecialCellDiff],
    side: str,
) -> None:
    """Annotates a PDF with change markers and saves to output_pdf_path."""
    doc = fitz.open(source_pdf_path)
    targets = _diffs_to_annotation_targets(section_diffs, item_diffs, special_cell_diffs, side)
    _annotate_document(doc, targets)
    _set_pdf_scroll_layout(doc)
    doc.save(output_pdf_path, garbage=4, deflate=True)
    doc.close()


# ---------------------------------------------------------------------------
# Side-by-Side Rendering
# ---------------------------------------------------------------------------

def _create_side_by_side_page(
    output_doc: fitz.Document,
    doc_before: fitz.Document,
    doc_after: fitz.Document,
    before_page_number: int | None,
    after_page_number: int | None,
) -> None:
    """Renders Before (left) and After (right) pages side by side on a landscape page.
    If one side has no corresponding page, that half shows a gray '(Not Exists)' placeholder.
    """
    ref_page = (
        doc_after[after_page_number] if after_page_number is not None
        else doc_before[before_page_number]
    )
    page_width = ref_page.rect.width
    page_height = ref_page.rect.height

    new_page = output_doc.new_page(width=page_width * 2, height=page_height)
    left_rect = fitz.Rect(0, 0, page_width, page_height)
    right_rect = fitz.Rect(page_width, 0, page_width * 2, page_height)

    if before_page_number is not None:
        new_page.show_pdf_page(left_rect, doc_before, before_page_number)
    else:
        shape = new_page.new_shape()
        shape.draw_rect(left_rect)
        shape.finish(fill=(0.93, 0.93, 0.93), fill_opacity=1.0, width=0)
        shape.commit()
        new_page.insert_text(
            fitz.Point(page_width / 2 - 35, page_height / 2),
            "(Not Exists)", fontsize=12, color=(0.55, 0.55, 0.55),
        )

    if after_page_number is not None:
        new_page.show_pdf_page(right_rect, doc_after, after_page_number)
    else:
        shape = new_page.new_shape()
        shape.draw_rect(right_rect)
        shape.finish(fill=(0.93, 0.93, 0.93), fill_opacity=1.0, width=0)
        shape.commit()
        new_page.insert_text(
            fitz.Point(page_width + page_width / 2 - 35, page_height / 2),
            "(Not Exists)", fontsize=12, color=(0.55, 0.55, 0.55),
        )

    # Vertical separator line between the two halves.
    shape = new_page.new_shape()
    shape.draw_line(fitz.Point(page_width, 0), fitz.Point(page_width, page_height))
    shape.finish(color=(0.4, 0.4, 0.4), width=1.0)
    shape.commit()


def _find_empty_section_pages(pdf_path: str) -> dict[tuple[str, str], list[int]]:
    """Finds pages that have a valid header but yield no extractable sections/items.

    These represent pages whose content was removed but whose page structure
    (header table, empty content area) still exists in the PDF.
    Returns (visit, page_name) → sorted list of physical page numbers.
    """
    empty_pages: dict[tuple, list[int]] = defaultdict(list)
    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            # Require at least a header table; pages with no tables cannot have a visit/page_name.
            if not tables:
                continue
            header_table = tables[0]
            if len(header_table) < 2:
                continue
            visit = _normalize_text(header_table[0][0] if header_table[0] else "")
            page_name = _normalize_text(header_table[1][0] if len(header_table) > 1 else "")
            if not visit or not page_name:
                continue
            if _extract_page_structure(page, page_index) is None:
                empty_pages[(visit, page_name)].append(page_index)
    return {key: sorted(pages) for key, pages in empty_pages.items()}


# ---------------------------------------------------------------------------
# Single Comparison PDF Assembly
# ---------------------------------------------------------------------------

def _build_single_comparison_pdf(
    file_before: str,
    file_after: str,
    output_path: str,
    structure_before: dict[tuple[str, str], ECRFPage],
    structure_after: dict[tuple[str, str], ECRFPage],
    page_diffs: list[PageDiff],
    section_diffs: list[SectionDiff],
    item_diffs: list[ItemDiff],
    special_cell_diffs: list[SpecialCellDiff] | None = None,
    empty_after_pages: dict[tuple[str, str], list[int]] | None = None,
) -> None:
    """Assembles a single comparison PDF.

    Page layout strategy:
      - Pages with any change (section or item) → side-by-side (Before left, After right)
      - Added/Deleted pages → side-by-side with '(Not Exists)' on the missing side
      - Unchanged pages → After page only (single width, no annotation)

    Page order: Before page order as primary; After-only pages inserted immediately after
    the last-seen matching After anchor (Excel Compare anchor algorithm).
    """
    # Determine which logical page keys have any changes.
    changed_page_keys: set[tuple[str, str]] = {
        (d.visit, d.page_name) for d in page_diffs
    } | {
        (d.visit, d.page_name) for d in section_diffs
    } | {
        (d.visit, d.page_name) for d in item_diffs
    }

    # Build merged logical page order using the Excel Compare anchor algorithm.
    before_order: list[tuple[str, str]] = sorted(
        structure_before.keys(),
        key=lambda key: structure_before[key].page_indices[0],
    )
    after_order: list[tuple[str, str]] = sorted(
        structure_after.keys(),
        key=lambda key: structure_after[key].page_indices[0],
    )
    before_key_set = set(structure_before.keys())

    final_order: list[tuple[str, str]] = before_order.copy()
    last_anchor: tuple[str, str] | None = None
    for key in after_order:
        if key not in before_key_set:
            if last_anchor is None:
                final_order.insert(0, key)
            else:
                try:
                    anchor_index = final_order.index(last_anchor)
                    final_order.insert(anchor_index + 1, key)
                except ValueError:
                    final_order.append(key)
        last_anchor = key

    # Use provided empty_after_pages or compute on demand.
    if empty_after_pages is None:
        empty_after_pages = _find_empty_section_pages(file_after)

    if special_cell_diffs is None:
        special_cell_diffs = []

    # Open source documents and annotate in memory.
    doc_before = fitz.open(file_before)
    doc_after = fitz.open(file_after)

    before_targets = _diffs_to_annotation_targets(section_diffs, item_diffs, special_cell_diffs, "before")
    after_targets = _diffs_to_annotation_targets(section_diffs, item_diffs, special_cell_diffs, "after")
    _annotate_document(doc_before, before_targets)
    _annotate_document(doc_after, after_targets)

    output_doc = fitz.open()
    rendered_after_pages: set[int] = set()

    # Track output page count after each Before/After physical page is rendered.
    # Used to find the correct insertion position for unmapped pages.
    before_pn_to_output_end: dict[int, int] = {}
    after_pn_to_output_end: dict[int, int] = {}

    for key in final_order:
        is_changed = key in changed_page_keys
        before_indices = structure_before[key].page_indices if key in structure_before else []
        after_indices = structure_after[key].page_indices if key in structure_after else []

        # For deleted pages with no After content pages, check for empty structure pages.
        effective_after = after_indices or empty_after_pages.get(key, [])

        needs_side_by_side = is_changed or not after_indices or not before_indices

        if needs_side_by_side:
            pair_count = max(len(before_indices), len(effective_after))
            if pair_count == 0:
                continue
            for pair_index in range(pair_count):
                before_pn = before_indices[pair_index] if pair_index < len(before_indices) else None
                after_pn = effective_after[pair_index] if pair_index < len(effective_after) else None
                _create_side_by_side_page(output_doc, doc_before, doc_after, before_pn, after_pn)
                if after_pn is not None:
                    rendered_after_pages.add(after_pn)
        else:
            # Unchanged page — insert After page at original size.
            for after_pn in after_indices:
                output_doc.insert_pdf(doc_after, from_page=after_pn, to_page=after_pn)
                rendered_after_pages.add(after_pn)

        # Record the output page count after this key's pages are added.
        current_output_end = len(output_doc)
        for before_pn in before_indices:
            before_pn_to_output_end[before_pn] = current_output_end
        for after_pn in (effective_after if needs_side_by_side else after_indices):
            after_pn_to_output_end[after_pn] = current_output_end

    # Insert unmapped pages (e.g. Schedule of Events, cover page) at their natural positions.
    # Each unmapped Before page is paired with the corresponding unmapped After page (by order).
    # After-only unmapped pages are positioned using the After structure.
    # All insertions are processed in descending order of target position to avoid index shifting.
    mapped_before_pages: set[int] = set(before_pn_to_output_end.keys())
    mapped_after_pages: set[int] = set(after_pn_to_output_end.keys())

    unmapped_before = sorted(
        pn for pn in range(len(doc_before)) if pn not in mapped_before_pages
    )
    unmapped_after = sorted(
        pn for pn in range(len(doc_after)) if pn not in rendered_after_pages
    )

    if unmapped_before or unmapped_after:
        pair_count = max(len(unmapped_before), len(unmapped_after))
        insertions: list[tuple[int, int, int | None, int | None]] = []

        for pair_index in range(pair_count):
            before_pn = unmapped_before[pair_index] if pair_index < len(unmapped_before) else None
            after_pn = unmapped_after[pair_index] if pair_index < len(unmapped_after) else None

            if before_pn is not None:
                # Anchor on Before position: insert after the last mapped Before page < before_pn.
                prev = max(
                    (b for b in mapped_before_pages if b < before_pn),
                    default=None,
                )
                insert_after = before_pn_to_output_end[prev] if prev is not None else 0
            else:
                # After-only page: anchor on After position.
                prev = max(
                    (a for a in mapped_after_pages if a < after_pn),
                    default=None,
                )
                insert_after = after_pn_to_output_end[prev] if prev is not None else 0

            insertions.append((insert_after, pair_index, before_pn, after_pn))

        # Build sets of physical page indices that have special cell diffs.
        # Used to decide whether an unmapped page pair needs side-by-side layout.
        changed_before_special_pns: set[int] = set()
        changed_after_special_pns: set[int] = set()
        for diff in special_cell_diffs:
            changed_before_special_pns.update(diff.page_indices_before)
            changed_after_special_pns.update(diff.page_indices_after)

        # Process in descending order of (insert_after, pair_index).
        # This ensures that inserting at a higher output position does not invalidate
        # the target indices for insertions at lower positions.
        # Within the same insert_after, higher pair_index is inserted first so the
        # final page order matches ascending pair_index (each new insert pushes others right).
        for insert_after, _, before_pn, after_pn in sorted(
            insertions, key=lambda item: (item[0], item[1]), reverse=True
        ):
            special_page_is_changed = (
                before_pn is None
                or after_pn is None
                or before_pn in changed_before_special_pns
                or after_pn in changed_after_special_pns
            )
            temp_doc = fitz.open()
            if special_page_is_changed:
                _create_side_by_side_page(temp_doc, doc_before, doc_after, before_pn, after_pn)
            else:
                # Unchanged special page — insert After page at original size.
                temp_doc.insert_pdf(doc_after, from_page=after_pn, to_page=after_pn)
            if insert_after >= len(output_doc):
                output_doc.insert_pdf(temp_doc)
            else:
                output_doc.insert_pdf(temp_doc, start_at=insert_after)
            temp_doc.close()

    _set_pdf_scroll_layout(output_doc)
    output_doc.save(output_path, garbage=4, deflate=True)
    output_doc.close()
    doc_before.close()
    doc_after.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compare_blank_ecrf(
    file_before: str,
    file_after: str,
    output_dir: str | None = None,
) -> str:
    """
    Compares two Blank eCRF PDF files and produces two annotated PDFs:
      - *_before_annotated.pdf: Before with deleted/modified items highlighted
      - *_after_annotated.pdf:  After with added/modified items highlighted

    Annotation colors:
      Red    = Deleted  (before PDF only)
      Green  = Added    (after PDF only)
      Yellow = Modified (both PDFs)

    Comparison hierarchy: Page → Section → Item.
    Added/Deleted pages and sections are annotated as blocks.
    """
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(file_before))

    base_before = os.path.splitext(os.path.basename(file_before))[0]
    base_after = os.path.splitext(os.path.basename(file_after))[0]
    output_before = os.path.join(output_dir, f"{base_before}_annotated.pdf")
    output_after = os.path.join(output_dir, f"{base_after}_annotated.pdf")

    structure_before = extract_ecrf_structure(file_before)
    structure_after = extract_ecrf_structure(file_after)

    if not structure_before:
        raise ValueError(
            f"No eCRF structure extracted from '{os.path.basename(file_before)}'. "
            "Verify it is a valid Blank eCRF PDF."
        )
    if not structure_after:
        raise ValueError(
            f"No eCRF structure extracted from '{os.path.basename(file_after)}'. "
            "Verify it is a valid Blank eCRF PDF."
        )

    page_diffs, section_diffs, item_diffs = compare_ecrf_structures(structure_before, structure_after)
    section_diffs = _generate_section_diffs_for_page_diffs(
        page_diffs, section_diffs, structure_before, structure_after,
        empty_after_pages=_find_empty_section_pages(file_after),
    )

    before_ecrf_indices = {pn for page in structure_before.values() for pn in page.page_indices}
    after_ecrf_indices = {pn for page in structure_after.values() for pn in page.page_indices}
    special_before = extract_special_pages(file_before, before_ecrf_indices)
    special_after = extract_special_pages(file_after, after_ecrf_indices)
    special_cell_diffs = compare_special_pages(special_before, special_after)

    before_section_diffs = [d for d in section_diffs if d.change_type == "Deleted"]
    before_item_diffs = [d for d in item_diffs if d.change_type in ("Deleted", "Modified")]
    before_special_cell_diffs = [d for d in special_cell_diffs if d.change_type in ("Deleted", "Modified")]
    _annotate_pdf(
        file_before, output_before, before_section_diffs, before_item_diffs, before_special_cell_diffs,
        side="before",
    )

    after_section_diffs_list = [d for d in section_diffs if d.change_type == "Added"]
    after_item_diffs = [d for d in item_diffs if d.change_type in ("Added", "Modified")]
    after_special_cell_diffs = [d for d in special_cell_diffs if d.change_type in ("Added", "Modified")]
    _annotate_pdf(
        file_after, output_after, after_section_diffs_list, after_item_diffs, after_special_cell_diffs,
        side="after",
    )

    added_pages = sum(1 for d in page_diffs if d.change_type == "Added")
    deleted_pages = sum(1 for d in page_diffs if d.change_type == "Deleted")
    added_sections = sum(1 for d in section_diffs if d.change_type == "Added")
    deleted_sections = sum(1 for d in section_diffs if d.change_type == "Deleted")
    added_items = sum(1 for d in item_diffs if d.change_type == "Added")
    deleted_items = sum(1 for d in item_diffs if d.change_type == "Deleted")
    modified_items = sum(1 for d in item_diffs if d.change_type == "Modified")
    added_special_cells = sum(1 for d in special_cell_diffs if d.change_type == "Added")
    deleted_special_cells = sum(1 for d in special_cell_diffs if d.change_type == "Deleted")
    modified_special_cells = sum(1 for d in special_cell_diffs if d.change_type == "Modified")

    return (
        f"Blank eCRF comparison complete.\n"
        f"Pages: {added_pages} added, {deleted_pages} deleted\n"
        f"Sections: {added_sections} added, {deleted_sections} deleted\n"
        f"Items: {added_items} added, {deleted_items} deleted, {modified_items} modified\n"
        f"Special page cells: {added_special_cells} added, {deleted_special_cells} deleted, "
        f"{modified_special_cells} modified\n"
        f"Annotated PDFs saved:\n"
        f"  Before: {output_before}\n"
        f"  After:  {output_after}"
    )


def compare_blank_ecrf_single_pdf(
    file_before: str,
    file_after: str,
    output_dir: str | None = None,
    output_file: str | None = None,
) -> str:
    """
    Compares two Blank eCRF PDFs and produces a single combined comparison PDF.

    Page assembly rules:
      - Pages with changes  → Before and After pages paired side by side (left = Before, right = After)
      - Added/Deleted pages → side-by-side with "(Not Exists)" on the missing side
      - Unchanged pages     → After pages only (no annotation)

    Annotation colors:
      Red    = Deleted items / sections (Before pages only)
      Green  = Added items / sections   (After pages only)
      Yellow = Modified items           (both Before and After pages)

    Output file: output_file if provided, otherwise {before}_vs_{after}_comparison.pdf in output_dir.
    Input PDFs must be text-based (not scanned).
    """
    if output_file is not None:
        output_path = output_file if os.path.isabs(output_file) else os.path.join(
            output_dir or os.path.dirname(os.path.abspath(file_before)), output_file
        )
    else:
        if output_dir is None:
            output_dir = os.path.dirname(os.path.abspath(file_before))
        base_before = os.path.splitext(os.path.basename(file_before))[0]
        base_after = os.path.splitext(os.path.basename(file_after))[0]
        output_path = os.path.join(output_dir, f"{base_before}_vs_{base_after}_comparison.pdf")

    structure_before = extract_ecrf_structure(file_before)
    structure_after = extract_ecrf_structure(file_after)

    if not structure_before:
        raise ValueError(
            f"No eCRF structure extracted from '{os.path.basename(file_before)}'. "
            "Verify it is a valid Blank eCRF PDF."
        )
    if not structure_after:
        raise ValueError(
            f"No eCRF structure extracted from '{os.path.basename(file_after)}'. "
            "Verify it is a valid Blank eCRF PDF."
        )

    page_diffs, section_diffs, item_diffs = compare_ecrf_structures(structure_before, structure_after)
    empty_after_pages = _find_empty_section_pages(file_after)
    section_diffs = _generate_section_diffs_for_page_diffs(
        page_diffs, section_diffs, structure_before, structure_after, empty_after_pages,
    )

    before_ecrf_indices = {pn for page in structure_before.values() for pn in page.page_indices}
    after_ecrf_indices = {pn for page in structure_after.values() for pn in page.page_indices}
    special_before = extract_special_pages(file_before, before_ecrf_indices)
    special_after = extract_special_pages(file_after, after_ecrf_indices)
    special_cell_diffs = compare_special_pages(special_before, special_after)

    _build_single_comparison_pdf(
        file_before, file_after, output_path,
        structure_before, structure_after,
        page_diffs, section_diffs, item_diffs,
        special_cell_diffs,
        empty_after_pages,
    )

    added_pages = sum(1 for d in page_diffs if d.change_type == "Added")
    deleted_pages = sum(1 for d in page_diffs if d.change_type == "Deleted")
    added_sections = sum(1 for d in section_diffs if d.change_type == "Added")
    deleted_sections = sum(1 for d in section_diffs if d.change_type == "Deleted")
    added_items = sum(1 for d in item_diffs if d.change_type == "Added")
    deleted_items = sum(1 for d in item_diffs if d.change_type == "Deleted")
    modified_items = sum(1 for d in item_diffs if d.change_type == "Modified")
    added_special_cells = sum(1 for d in special_cell_diffs if d.change_type == "Added")
    deleted_special_cells = sum(1 for d in special_cell_diffs if d.change_type == "Deleted")
    modified_special_cells = sum(1 for d in special_cell_diffs if d.change_type == "Modified")

    return (
        f"Blank eCRF comparison complete (single PDF mode).\n"
        f"Pages: {added_pages} added, {deleted_pages} deleted\n"
        f"Sections: {added_sections} added, {deleted_sections} deleted\n"
        f"Items: {added_items} added, {deleted_items} deleted, {modified_items} modified\n"
        f"Special page cells: {added_special_cells} added, {deleted_special_cells} deleted, "
        f"{modified_special_cells} modified\n"
        f"Comparison PDF saved: {output_path}"
    )
