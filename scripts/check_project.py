import ast
import io
from pathlib import Path
import re
import tokenize
import tomllib


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORTS = {"video_baselines", "torch", "optuna", "numpy", "yaml"}


def check_python():
    paths = [path for directory in ("src", "tests", "examples", "scripts")
             for path in (ROOT / directory).rglob("*.py")]
    for path in paths:
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        comments = [token for token in tokenize.generate_tokens(io.StringIO(source).readline)
                    if token.type == tokenize.COMMENT]
        if comments:
            raise ValueError(f"Code comments are not allowed: {path.relative_to(ROOT)}")
        if path.is_relative_to(ROOT / "src"):
            imports = [node.module for node in ast.walk(tree)
                       if isinstance(node, ast.ImportFrom) and node.module]
            imports += [alias.name for node in ast.walk(tree)
                        if isinstance(node, ast.Import) for alias in node.names]
            if any(name.split(".")[0] in FORBIDDEN_IMPORTS for name in imports):
                raise ValueError(f"Business dependency in core: {path.relative_to(ROOT)}")
    return len(paths)


def check_links():
    documents = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CHANGELOG.md", *(ROOT / "docs").glob("*.md")]
    for document in documents:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", document.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            relative = target.split("#", 1)[0]
            if relative and not (document.parent / relative).exists():
                raise ValueError(f"Broken link in {document.relative_to(ROOT)}: {target}")


def check_metadata():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    if project["name"] != "pygpu-scheduling" or project["dependencies"]:
        raise ValueError("Distribution name or core runtime dependencies changed")
    if project["license"] != "MIT" or not (ROOT / "LICENSE").is_file():
        raise ValueError("MIT license metadata or license file is missing")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", project["version"]):
        raise ValueError("Version must have a release-compatible three-part format")


def main():
    count = check_python()
    check_links()
    check_metadata()
    print(f"Project checks passed: {count} Python files, import boundaries, metadata and documentation links")


if __name__ == "__main__":
    main()
