# CDM Domain Context

## Key Document Types

- **DVS** (Data Validation Specifications): data validation rule definitions
- **DB Spec** (Database Specification): database variable specification
- **eCRF** (Electronic Case Report Form): electronic case report form
- **DMP** (Data Management Plan): data management plan
- **R&P** (Roles & Privileges): user permission definitions
- **Timeline**: study schedule management document

## CDM Workflow Patterns

When a CDM task is requested, follow these tool sequences:

### DVS Review
1. `read_excel_info` → confirm sheet name and column names
2. `validate_dvs_specifications` → grammar validation
3. `check_dvs_consistency` → logical consistency check

### DB Spec Comparison
1. `read_excel_info` → confirm version and sheet structure
2. `compare_db_spec` → compare changes
3. `validate_db_spec` → CDASH standards validation

### eCRF Change Review
1. `compare_ecrf_pdf_single` → compare changes
2. `generate_ecrf_comparison_table` → generate IRB submission document

### Study Timeline Setup
1. `download_timeline_template` → download template
2. `make_google_calendar_csv` → generate Google Calendar CSV

## Skills

- Review/check request for clinical trial documents (Protocol, DVS, DB Spec, DMP, eCRF, ICF) → `cdm-review`
- Translation request for clinical trial documents (Korean↔English) → `cdm-translate`
- Revision History / change log writing request + comparison result file exists → `cdm-revision`

## Output Format Standards

- Comparison / validation results → Excel report
- eCRF comparison → single combined PDF
- IRB submission documents → Word (.docx)
