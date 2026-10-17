"""
CDASH Reference Data Generator and Loader

Extracts CDASH variable and domain definitions from CDASH/SDTMIG Excel files
and stores them as a versioned JSON file for use by the DB Spec validator.

JSON structure:
  {
    "metadata": {
      "sources": [{"version": "...", "file": "...", "generated_at": "..."}],
      "last_updated": "..."
    },
    "suffixes":   {"ORRES": "Char", "DOSE": "Num", ...},
    "standalone": {"BRTHDAT": "Char", "AGE": "Num", ...},
    "domains": {
      "cdashig":    ["AE", "AG", ...],
      "sdtmig_only": ["BE", "BS", ...]
    }
  }
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

_DATA_FILE = Path(__file__).parent.parent / "data" / "cdash_variables.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_variables(input_file: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """
    Reads a CDASH Model Excel file (Variables sheet) and returns:
      (version, suffixes, standalone)

    suffixes   : {suffix_without_dashes: Type}  — from --XXX variables
    standalone : {variable_name: Type}           — from non-prefixed variables
    """
    dataframe = pd.read_excel(input_file, sheet_name="Variables", header=0)

    version = "Unknown"
    if "Version" in dataframe.columns:
        unique_versions = dataframe["Version"].dropna().unique()
        if len(unique_versions) > 0:
            version = str(unique_versions[0]).strip()

    suffixes: dict[str, str] = {}
    standalone: dict[str, str] = {}

    for _, row in dataframe.iterrows():
        var = str(row.get("CDASH Variable", "")).strip()
        var_type = str(row.get("Type", "")).strip()

        if not var or var == "nan" or not var_type or var_type == "nan":
            continue

        if var.startswith("--"):
            suffix = var[2:]
            if suffix:
                suffixes[suffix] = var_type
        else:
            standalone[var] = var_type

    return version, suffixes, standalone


def _extract_domains(
    cdashig_file: str,
    sdtmig_file: str,
) -> tuple[list[str], list[str]]:
    """
    Extracts domain lists from CDASHIG and SDTMIG Excel files.

    Returns:
      (cdashig_domains, sdtmig_only_domains)
    """
    cdashig_df = pd.read_excel(cdashig_file, sheet_name="Variables", header=0)
    cdashig_domains = sorted(
        str(d) for d in cdashig_df["Domain"].dropna().unique()
    )

    sdtmig_df = pd.read_excel(sdtmig_file, sheet_name="Variables", header=0)
    sdtmig_all = {str(d) for d in sdtmig_df["Dataset Name"].dropna().unique()}
    sdtmig_only = sorted(sdtmig_all - set(cdashig_domains))

    return cdashig_domains, sdtmig_only


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_cdash_variables(
    input_file: str,
    cdashig_file: str | None = None,
    sdtmig_file: str | None = None,
    merge: bool = False,
    output_file: str | None = None,
) -> str:
    """
    Generates or updates the CDASH reference JSON from CDASH Model / CDASHIG / SDTMIG Excel files.

    Args:
        input_file  : Path to the CDASH Model Excel file (e.g. CDASH_Model_v1.3.xlsx).
                      Extracts suffixes and standalone variable types.
        cdashig_file: Path to the CDASHIG Excel file (e.g. CDASHIG_v2.3.xlsx).
                      Required together with sdtmig_file to update domain lists.
        sdtmig_file : Path to the SDTMIG Excel file (e.g. SDTMIG_v3.4.xlsx).
                      Required together with cdashig_file to update domain lists.
        merge       : If True, merges with existing data — new values override conflicts.
                      If False (default), replaces the file entirely.
        output_file : Output JSON path. Defaults to the built-in package data file.

    Returns:
        Summary string describing the result.
    """
    output_path = Path(output_file) if output_file else _DATA_FILE
    version, suffixes, standalone = _extract_variables(input_file)

    source_entry = {
        "version": version,
        "file": Path(input_file).name,
        "generated_at": datetime.now().isoformat(),
    }
    now = datetime.now().isoformat()

    if merge and output_path.exists():
        with open(output_path, encoding="utf-8") as file:
            data = json.load(file)
        data["metadata"]["sources"].append(source_entry)
        data["metadata"]["last_updated"] = now
        data["suffixes"].update(suffixes)
        data["standalone"].update(standalone)
    else:
        data = {
            "metadata": {
                "sources": [source_entry],
                "last_updated": now,
            },
            "suffixes": suffixes,
            "standalone": standalone,
            "domains": {},
        }

    if cdashig_file and sdtmig_file:
        cdashig_domains, sdtmig_only = _extract_domains(cdashig_file, sdtmig_file)
        data["domains"] = {
            "cdashig": cdashig_domains,
            "sdtmig_only": sdtmig_only,
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    source_labels = ", ".join(s["version"] for s in data["metadata"]["sources"])
    domain_summary = (
        f"\n  Domains  : {len(data['domains'].get('cdashig', []))} CDASHIG"
        f" + {len(data['domains'].get('sdtmig_only', []))} SDTMIG-only"
        if data.get("domains")
        else ""
    )
    return (
        f"CDASH reference data saved: {output_path.name}\n"
        f"  Sources  : {source_labels}\n"
        f"  Suffixes : {len(data['suffixes'])}\n"
        f"  Standalone variables: {len(data['standalone'])}"
        f"{domain_summary}"
    )


def load_cdash_variables() -> tuple[dict[str, str], dict[str, str], dict[str, list[str]]]:
    """
    Loads CDASH reference data from the package data JSON file.

    Returns:
        (suffixes, standalone, domains) where:
        - suffixes  : {suffix: expected_type}
        - standalone: {variable_name: expected_type}
        - domains   : {"cdashig": [...], "sdtmig_only": [...]}

    Raises:
        FileNotFoundError if the JSON has not been generated yet.
    """
    if not _DATA_FILE.exists():
        raise FileNotFoundError(
            f"CDASH reference data not found: {_DATA_FILE}\n"
            "Run the generate_cdash_variables tool to generate it first."
        )
    with open(_DATA_FILE, encoding="utf-8") as file:
        data = json.load(file)
    return data["suffixes"], data["standalone"], data.get("domains", {})
