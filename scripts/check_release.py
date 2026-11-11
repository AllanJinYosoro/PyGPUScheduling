from pathlib import Path
import sys
import tomllib


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/check_release.py v<version>")
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    expected = f"v{version}"
    if sys.argv[1] != expected:
        raise SystemExit(f"Release tag must be {expected}, got {sys.argv[1]}")
    print(f"Release tag matches package version: {expected}")


if __name__ == "__main__":
    main()
