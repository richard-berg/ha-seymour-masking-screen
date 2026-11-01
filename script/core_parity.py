#!/usr/bin/env python3
"""Check the integration with the tooling of Home Assistant Core.

Custom integrations aren't held to the integration quality scale, so this
copies the integration and its tests into a Home Assistant Core checkout as
if it were built in, and runs the checks Core runs on its own integrations:
hassfest (including the quality scale tier), ruff, mypy, pylint, and the
tests with full coverage.

Usage: script/core_parity.py <path to a home-assistant/core checkout>

Run it from the virtual environment of that checkout, with its test
requirements installed. The checkout is modified; use a throwaway one.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

DOMAIN = "seymour_masking_screen"
REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "custom_components" / DOMAIN
TESTS = REPO / "tests"

# Only custom integrations carry these, and Core expects its own docs URL.
CUSTOM_ONLY_MANIFEST_KEYS = ("issue_tracker", "version")
CORE_DOCUMENTATION = f"https://www.home-assistant.io/integrations/{DOMAIN}"
# Core keeps generated translations and brand images elsewhere.
NOT_IN_CORE = ("brand", "translations", "__pycache__")


def run(core: Path, *args: str) -> None:
    """Run a command in the Core checkout, and stop on failure."""
    print(f"\n$ {' '.join(args)}", flush=True)
    if subprocess.run(args, cwd=core, check=False).returncode:
        sys.exit(f"Failed: {' '.join(args)}")


def install(core: Path) -> None:
    """Copy the integration and its tests into the Core checkout."""
    component = core / "homeassistant" / "components" / DOMAIN
    tests = core / "tests" / "components" / DOMAIN
    for target in (component, tests):
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(SOURCE, component, ignore=shutil.ignore_patterns(*NOT_IN_CORE))
    shutil.copytree(TESTS, tests, ignore=shutil.ignore_patterns("__pycache__"))

    manifest_path = component / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for key in CUSTOM_ONLY_MANIFEST_KEYS:
        manifest.pop(key, None)
    manifest["documentation"] = CORE_DOCUMENTATION
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    strict_typing = core / ".strict-typing"
    entry = f"homeassistant.components.{DOMAIN}.*"
    lines = strict_typing.read_text().splitlines()
    if entry not in lines:
        # Core keeps the list sorted after its leading comment block
        comments = [line for line in lines if line.startswith("#")]
        entries = sorted([line for line in lines if not line.startswith("#")] + [entry])
        strict_typing.write_text("\n".join(comments + entries) + "\n")


def check_translations(core: Path) -> None:
    """Fail if the shipped English translations are out of date."""
    generated = core / "homeassistant" / "components" / DOMAIN / "translations"
    expected = json.loads((generated / "en.json").read_text())
    shipped = json.loads((SOURCE / "translations" / "en.json").read_text())
    if expected != shipped:
        sys.exit(
            "translations/en.json is out of date. Copy it from "
            f"{generated / 'en.json'} after running this script."
        )
    print("\ntranslations/en.json is up to date", flush=True)


def main() -> None:
    """Run the checks."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("core", type=Path, help="path to a Core checkout")
    core = parser.parse_args().core.resolve()

    install(core)
    component = f"homeassistant/components/{DOMAIN}"
    tests = f"tests/components/{DOMAIN}"
    python = sys.executable

    # Regenerate the files Core derives from manifests, then validate
    run(core, python, "-m", "script.hassfest", "--skip-plugins", "quality_scale")
    run(core, python, "-m", "script.gen_requirements_all")
    run(core, python, "-m", "script.hassfest", "--integration-path", component)
    run(core, python, "-m", "script.translations", "develop", "--all")
    check_translations(core)
    run(core, python, "-m", "ruff", "check", component, tests)
    run(core, python, "-m", "ruff", "format", "--check", component, tests)
    # Only report errors in this integration; other Core modules may lack the
    # optional requirements they're type checked against in Core's own CI.
    run(core, python, "-m", "mypy", "--follow-imports=silent", component)
    run(core, python, "-m", "pylint", component, tests)
    run(
        core,
        python,
        "-m",
        "pytest",
        tests,
        "-p",
        "no:cacheprovider",
        "--timeout=30",
        f"--cov=homeassistant.components.{DOMAIN}",
        "--cov-report=term-missing",
        "--cov-fail-under=100",
    )
    print("\nAll Core checks passed", flush=True)


if __name__ == "__main__":
    main()
