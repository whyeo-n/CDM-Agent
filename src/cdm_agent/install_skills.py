"""CLI command to install CDM skills into AI agent tool directories."""

import argparse
import shutil
import sys
from importlib.resources import files
from pathlib import Path

TOOL_SKILL_PATHS = {
    "claude": Path(".claude") / "skills",
    "gemini": Path(".gemini") / "skills",
    "codex": Path(".agents") / "skills",
}


def detect_installed_tools(working_directory: Path) -> list[str]:
    """Detect which AI tools are configured in the working directory."""
    detected_tools = []
    for tool_name, skill_path in TOOL_SKILL_PATHS.items():
        tool_config_directory = working_directory / skill_path.parts[0]
        if tool_config_directory.exists():
            detected_tools.append(tool_name)
    return detected_tools


def install_skills_for_tool(tool_name: str, working_directory: Path) -> None:
    """Copy bundled skill directories into the target tool's skills folder."""
    target_skills_directory = working_directory / TOOL_SKILL_PATHS[tool_name]
    target_skills_directory.mkdir(parents=True, exist_ok=True)

    skills_package = files("cdm_agent.skills")
    bundled_skills_path = Path(str(skills_package))

    installed_skill_names = []
    for skill_directory in bundled_skills_path.iterdir():
        if not skill_directory.is_dir() or skill_directory.name.startswith("_"):
            continue
        skill_name = skill_directory.name
        destination = target_skills_directory / skill_name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(skill_directory, destination)
        installed_skill_names.append(skill_name)

    print(f"[{tool_name}] Installed {len(installed_skill_names)} skill(s) → {target_skills_directory}")
    for skill_name in sorted(installed_skill_names):
        print(f"  - {skill_name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cdm-install-skills",
        description="Install CDM skills into AI agent tool directories (.claude, .gemini, .agents).",
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
        help="Install for all supported tools.",
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
                "Use --tool <claude|gemini|codex> to specify a target, or --all to install for all."
            )
            sys.exit(1)
        print(f"Auto-detected tool(s): {', '.join(target_tools)}")

    for tool_name in target_tools:
        install_skills_for_tool(tool_name, working_directory)


if __name__ == "__main__":
    main()
