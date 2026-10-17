"""CLI command to initialize CDM agent setup: install skills and agent config files."""

import argparse
import shutil
import sys
from importlib.resources import files
from pathlib import Path

from cdm_agent.install_skills import TOOL_SKILL_PATHS, detect_installed_tools, install_skills_for_tool

TOOL_CONFIG_FILES = {
    "claude": [Path(".claude") / "rules" / "cdm-domain.md"],
    "gemini": [Path("GEMINI.md")],
    "codex": [Path("AGENTS.md")],
}


def install_config_for_tool(tool_name: str, working_directory: Path) -> None:
    """Copy bundled agent config files into the target tool's config location."""
    configs_package = files("cdm_agent.agent_configs") / tool_name
    configs_path = Path(str(configs_package))

    installed_files = []
    for config_file in TOOL_CONFIG_FILES[tool_name]:
        source = configs_path / config_file.name
        if not source.exists():
            source = _find_file_recursive(configs_path, config_file.name)
        if source is None:
            continue

        destination = working_directory / config_file
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            print(f"  [skip] {config_file} already exists — not overwritten")
            continue

        shutil.copy2(source, destination)
        installed_files.append(config_file)

    if installed_files:
        print(f"[{tool_name}] Installed config file(s):")
        for file_path in installed_files:
            print(f"  - {file_path}")


def _find_file_recursive(directory: Path, filename: str) -> Path | None:
    for file_path in directory.rglob(filename):
        return file_path
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cdm-init",
        description="Initialize CDM agent setup: install skills and agent config files.",
    )
    parser.add_argument(
        "--tool",
        choices=list(TOOL_SKILL_PATHS.keys()),
        help="Target AI tool. If omitted, auto-detects from current directory.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="install_all",
        help="Initialize for all supported tools.",
    )
    arguments = parser.parse_args()

    working_directory = Path.cwd()

    if arguments.install_all:
        target_tools = list(TOOL_SKILL_PATHS.keys())
    elif arguments.tool:
        target_tools = [arguments.tool]
    else:
        target_tools = detect_installed_tools(working_directory)
        if not target_tools:
            print(
                "No AI tool configuration found in current directory.\n"
                "Use --tool <claude|gemini|codex> to specify a target, or --all to initialize for all."
            )
            sys.exit(1)
        print(f"Auto-detected tool(s): {', '.join(target_tools)}")

    for tool_name in target_tools:
        install_skills_for_tool(tool_name, working_directory)
        install_config_for_tool(tool_name, working_directory)


if __name__ == "__main__":
    main()
