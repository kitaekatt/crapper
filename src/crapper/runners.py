"""Run each language's coverage tool, then leave the reports for the loader.

Failures are reported and do not stop analysis. A language with no coverage
tool, or a failed run, scores its functions at 0%.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from crapper.discover import is_test_file, language_of

_MAVEN = [
    "mvn",
    "-q",
    "org.jacoco:jacoco-maven-plugin:0.8.12:prepare-agent",
    "test",
    "org.jacoco:jacoco-maven-plugin:0.8.12:report",
]


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def _show(command: str | list[str]) -> str:
    if isinstance(command, str):
        return command
    return " ".join(shlex.quote(part) for part in command)


def run_shell(command: str | list[str], cwd: Path) -> int:
    """Run a coverage command.

    A list is an argument vector and does not go through a shell, so a path
    with spaces or metacharacters stays one argument. A string is a command
    the user typed in `--coverage-command` and runs in a shell.
    """

    _warn(f"+ ({cwd}) {_show(command)}")
    try:
        if isinstance(command, str):
            completed = subprocess.run(command, cwd=cwd, shell=True)
        else:
            completed = subprocess.run(list(command), cwd=cwd)
    except OSError as exc:
        _warn(f"Coverage command failed to start: {exc}")
        return 127
    return completed.returncode


def nearest_marker(start: Path, marker: str, stop: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    stop = stop.resolve()
    while True:
        if (current / marker).is_file():
            return current
        if current == stop or current.parent == current:
            return None
        current = current.parent


def _clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


_COVERAGE_DIRS = {"typescript", "rust", "go", "python"}


def _outside_language_dirs(coverage: Path, path: Path) -> bool:
    relative = path.relative_to(coverage)
    if path.name in _COVERAGE_DIRS:
        return False
    return not _COVERAGE_DIRS.intersection(relative.parts)


def _remove_cloverage_html(coverage: Path) -> None:
    for path in coverage.rglob("*.html"):
        if not _outside_language_dirs(coverage, path):
            continue
        path.unlink()


def _remove_empty_dirs(coverage: Path) -> None:
    directories = [path for path in coverage.rglob("*") if path.is_dir()]
    for path in sorted(directories, reverse=True):
        if not _outside_language_dirs(coverage, path):
            continue
        try:
            path.rmdir()
        except OSError:
            continue


def _clean_clojure(root: Path) -> None:
    """Remove Cloverage's HTML and top-level lcov without touching other reports.

    Other languages keep their directories. Files that are not Cloverage HTML
    stay, including anything a project keeps under target/coverage itself.
    """

    coverage = root / "target" / "coverage"
    if not coverage.is_dir():
        return
    lcov = coverage / "lcov.info"
    if lcov.is_file():
        lcov.unlink()
    _remove_cloverage_html(coverage)
    _remove_empty_dirs(coverage)


_VITEST_CONFIGS = (
    "vitest.config.ts",
    "vitest.config.mts",
    "vitest.config.cts",
    "vitest.config.js",
    "vitest.config.mjs",
    "vitest.config.cjs",
)


class PackageJsonError(Exception):
    """package.json is present and cannot be read as an object."""


def _package_json(package: Path) -> dict | None:
    path = package / "package.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackageJsonError(f"{path}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise PackageJsonError(f"{path}: expected a JSON object")
    return data


def _uses_vitest(package: Path, scripts: dict) -> bool:
    if "vitest" in str(scripts.get("test", "")):
        return True
    return any((package / name).is_file() for name in _VITEST_CONFIGS)


def _vitest_version(package: Path) -> str | None:
    installed = package / "node_modules" / "vitest" / "package.json"
    if installed.is_file():
        try:
            data = json.loads(installed.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _warn(f"{installed}: {exc.msg}")
            data = None
        if isinstance(data, dict) and isinstance(data.get("version"), str):
            return data["version"]
    return None


def _manifest_snapshot(package: Path) -> dict[Path, bytes | None]:
    snapshot: dict[Path, bytes | None] = {}
    for name in ("package.json", "package-lock.json", "npm-shrinkwrap.json"):
        path = package / name
        snapshot[path] = path.read_bytes() if path.is_file() else None
    return snapshot


def _restore_manifests(snapshot: dict[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        if content is None:
            if path.is_file():
                path.unlink()
        elif not path.is_file() or path.read_bytes() != content:
            path.write_bytes(content)


def _ensure_vitest_coverage(package: Path) -> bool:
    """Vitest collects coverage itself. c8 never sees its worker processes."""

    if (package / "node_modules" / "@vitest" / "coverage-v8").is_dir():
        return True
    version = _vitest_version(package)
    spec = f"@vitest/coverage-v8@{version}" if version else "@vitest/coverage-v8"
    _warn(
        f"Installing {spec} so Vitest can write LCOV. "
        "package.json and the lockfile are restored afterward."
    )
    snapshot = _manifest_snapshot(package)
    try:
        code = run_shell(
            ["npm", "install", "--no-save", "--no-package-lock", spec],
            package,
        )
    finally:
        _restore_manifests(snapshot)
    ready = code == 0 and (package / "node_modules" / "@vitest" / "coverage-v8").is_dir()
    if not ready:
        _warn("Vitest's coverage provider is missing. TypeScript coverage will score 0%.")
    return ready


def typescript_packages(root: Path, files: list[Path]) -> list[Path]:
    packages: set[Path] = set()
    root = root.resolve()
    for file in files:
        if language_of(file) != "typescript":
            continue
        package = nearest_marker(file, "package.json", root)
        if package is not None:
            packages.add(package.resolve())
    return sorted(packages)


def typescript_command(
    package: Path, files: list[Path], report_dir: Path
) -> list[str] | None:
    """Argument vector that writes LCOV into `report_dir`, or None when there is no test script."""

    data = _package_json(package)
    if data is None:
        return None
    scripts = data.get("scripts") or {}
    if not isinstance(scripts, dict):
        scripts = {}
    if "coverage" in scripts:
        return ["npm", "run", "coverage"]
    if _uses_vitest(package, scripts):
        return _vitest_coverage_command(package, files, report_dir)
    if "test" not in scripts:
        return None
    return [
        "npx",
        "--yes",
        "c8",
        "--reporter=lcov",
        "--reports-dir",
        str(report_dir),
        "npm",
        "test",
    ]


def _vitest_includes(package: Path, files: list[Path]) -> list[str]:
    includes: list[str] = []
    package = package.resolve()
    for file in files:
        if language_of(file) != "typescript" or is_test_file(file):
            continue
        try:
            relative = Path(file).resolve().relative_to(package)
        except ValueError:
            continue
        includes.append(relative.as_posix())
    return includes


def _vitest_coverage_command(package: Path, files: list[Path], report_dir: Path) -> list[str]:
    binary = package / "node_modules" / ".bin" / "vitest"
    if binary.is_file():
        command = [str(binary)]
    else:
        command = ["npx", "vitest"]
    command.extend(
        [
            "run",
            "--coverage",
            "--coverage.reporter=lcov",
            f"--coverage.reportsDirectory={report_dir}",
            "--coverage.reportOnFailure=true",
        ]
    )
    for include in _vitest_includes(package, files):
        command.append(f"--coverage.include={include}")
    return command


def _coverage_report(root: Path, module: Path, language: str) -> Path:
    root = root.resolve()
    module = module.resolve()
    if module == root:
        return root / "target" / "coverage" / language / "lcov.info"
    slug = module.relative_to(root).as_posix().replace("/", "__")
    return root / "target" / "coverage" / language / slug / "lcov.info"


def rust_modules(root: Path, files: list[Path]) -> list[Path]:
    root = root.resolve()
    return [
        module.resolve()
        for module in _modules_with(files, ".rs", "Cargo.toml", root)
    ]


def _rust_kind() -> str | None:
    if shutil.which("cargo-llvm-cov"):
        return "llvm-cov"
    if shutil.which("cargo-tarpaulin"):
        return "tarpaulin"
    _warn("Neither cargo-llvm-cov nor cargo-tarpaulin is installed. Installing cargo-llvm-cov.")
    if shutil.which("rustup"):
        code = run_shell(["rustup", "component", "add", "llvm-tools-preview"], Path.home())
        if code != 0:
            _warn("rustup component add llvm-tools-preview failed.")
    code = run_shell(["cargo", "install", "cargo-llvm-cov", "--locked"], Path.home())
    if code == 0 and shutil.which("cargo-llvm-cov"):
        return "llvm-cov"
    _warn("Rust coverage will score 0%.")
    return None


_PYTHON_MARKERS = ("pyproject.toml", "setup.cfg", "setup.py", "pytest.ini", "tox.ini")


def python_roots(root: Path, files: list[Path], tests: str | None = None) -> list[Path]:
    """Directories to run Python tests from.

    Each source file belongs to its nearest project marker. With `tests`, the
    tests live outside those projects, so one run from `root` covers them all.
    """

    found: set[Path] = set()
    root = root.resolve()
    for file in files:
        if Path(file).suffix != ".py":
            continue
        module = None
        for marker in _PYTHON_MARKERS:
            module = nearest_marker(Path(file), marker, root)
            if module is not None:
                break
        found.add((module or root).resolve())
    if tests is not None and found:
        return [root]
    return sorted(found)


_SYSTEM_PYTHON = "python3"
_PROJECT_VENV_PYTHONS = (".venv/bin/python", "venv/bin/python")


def _python_executable(package: Path) -> str:
    for relative in _PROJECT_VENV_PYTHONS:
        candidate = package / relative
        if candidate.is_file():
            return str(candidate)
    return _SYSTEM_PYTHON


def uses_pytest(package: Path, tests: str | None = None) -> bool:
    if (package / "pytest.ini").is_file() or (package / "conftest.py").is_file():
        return True
    if tests is not None and (package / tests / "conftest.py").is_file():
        return True
    for name in ("pyproject.toml", "setup.cfg", "tox.ini"):
        path = package / name
        if path.is_file() and "pytest" in path.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def python_sources(package: Path, files: list[Path]) -> str:
    """Directories coverage.py should report even when a file was never imported."""

    tops: set[str] = set()
    package = package.resolve()
    for file in files:
        path = Path(file)
        if path.suffix != ".py" or is_test_file(path):
            continue
        try:
            relative = path.resolve().relative_to(package)
        except ValueError:
            continue
        tops.add(_source_name(relative))
    return ",".join(sorted(tops))


def _source_name(relative: Path) -> str:
    """A top-level module is named without .py, which coverage.py --source requires."""

    if len(relative.parts) == 1:
        return relative.stem
    return relative.parts[0]


def python_coverage_commands(
    py: str,
    kind: str,
    data_file: Path,
    report: Path,
    source: str,
    *,
    tests: str | None = None,
    rcfile: Path | None = None,
) -> tuple[list[str], list[str]]:
    run = [py, "-m", "coverage", "run"]
    if rcfile is not None:
        run.append(f"--rcfile={rcfile}")
    run.append(f"--data-file={data_file}")
    if source:
        run.append(f"--source={source}")
    if kind == "pytest":
        run.extend(["-m", "pytest"])
        if tests is not None:
            run.append(tests)
    else:
        run.extend(["-m", "unittest", "discover", "-s", tests or "."])
    lcov = [py, "-m", "coverage", "lcov", f"--data-file={data_file}", "-o", str(report)]
    return run, lcov


def python_combine_command(py: str, data_file: Path) -> list[str]:
    return [py, "-m", "coverage", "combine", f"--data-file={data_file}"]


# coverage.py measures child processes with `[run] patch = subprocess`.
# 7.10.0 added it; 7.10.3 passes options given on the command line (--data-file,
# --source) to the children; 7.10.6 passes source directories to children that
# run in another directory.
_SUBPROCESS_MIN_VERSION = (7, 10, 6)
_PROBE_TOO_OLD = 3
_PROBE_NO_PATCH = 4
_VERSION_PROBE = (
    f"import sys, coverage; sys.exit({_PROBE_TOO_OLD} "
    f"if coverage.version_info < {_SUBPROCESS_MIN_VERSION} else 0)"
)
_PATCH_PROBE = (
    "import sys, coverage; sys.exit(0 if 'subprocess' in "
    f"coverage.Coverage().get_option('run:patch') else {_PROBE_NO_PATCH})"
)
_SUBPROCESS_RCFILE = "[run]\nparallel = True\npatch = subprocess\n"


def has_coverage_config(package: Path) -> bool:
    """Does the project carry its own coverage.py configuration?"""

    if os.environ.get("COVERAGE_RCFILE") or (package / ".coveragerc").is_file():
        return True
    for name, section in (
        ("pyproject.toml", "[tool.coverage"),
        ("setup.cfg", "[coverage:"),
        ("tox.ini", "[coverage:"),
    ):
        path = package / name
        if path.is_file() and section in path.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def _subprocess_rcfile(py: str, package: Path, directory: Path) -> tuple[bool, Path | None]:
    """Prepare the coverage configuration that measures child processes.

    Returns (usable, rcfile). A project's own configuration is never edited or
    replaced: it must enable `patch = subprocess` itself and rcfile is None.
    Without one, crapper writes a configuration that does and returns its path.
    """

    own = has_coverage_config(package)
    probe = run_shell([py, "-c", _VERSION_PROBE], package)
    if probe == 0 and own:
        probe = run_shell([py, "-c", _PATCH_PROBE], package)
    if probe == _PROBE_TOO_OLD:
        _warn(
            "--python-subprocess-coverage needs coverage.py "
            f"{'.'.join(map(str, _SUBPROCESS_MIN_VERSION))} or newer in {py}. "
            "Upgrade coverage there."
        )
        return False, None
    if probe == _PROBE_NO_PATCH:
        _warn(
            f"{package} has its own coverage configuration, which crapper does not edit. "
            "Add `patch = subprocess` to its [run] section ([tool.coverage.run] in "
            "pyproject.toml) for --python-subprocess-coverage."
        )
        return False, None
    if probe != 0:
        _warn(f"Checking coverage.py in {py} exited {probe}.")
        return False, None
    if own:
        return True, None
    rcfile = directory / ".coveragerc"
    rcfile.write_text(_SUBPROCESS_RCFILE, encoding="utf-8")
    return True, rcfile


PYTHON_INSTALLERS = ("pip", "uv")


def python_install_command(py: str, module: str, installer: str = "pip") -> list[str]:
    """The command that installs `module` into the interpreter `py`."""

    if installer == "uv":
        return ["uv", "pip", "install", "--python", py, module]
    return [py, "-m", "pip", "install", "--disable-pip-version-check", module]


def _is_project_venv(py: str, package: Path) -> bool:
    return py in {str(package / relative) for relative in _PROJECT_VENV_PYTHONS}


def _manual_install_hint(py: str, module: str, installer: str) -> str:
    if installer == "uv":
        return f"uv pip install --python {py} {module}"
    return f"{py} -m pip install {module}"


def _ensure_python_module(py: str, package: Path, module: str, installer: str = "pip") -> bool:
    """Import `module`, installing it only into a project virtual environment.

    Any other interpreter, such as the `python3` fallback, belongs to the user,
    so a missing module is reported there and never installed, whichever
    installer was chosen.
    """

    if run_shell([py, "-c", f"import {module}"], package) == 0:
        return True
    if not _is_project_venv(py, package):
        _warn(
            f"{module} is not installed in {py}, and crapper does not install packages "
            "outside a project virtual environment. "
            f"Run `{_manual_install_hint(py, module, installer)}`, "
            f"or create a .venv in {package}."
        )
        return False
    if installer == "uv" and shutil.which("uv") is None:
        _warn(
            f"--python-installer uv was chosen, but `uv` is not on PATH, so {module} was "
            "not installed. Install uv, or use --python-installer pip."
        )
        return False
    _warn(
        f"Installing {module} for Python coverage. "
        "The project requirements are left unchanged."
    )
    return run_shell(python_install_command(py, module, installer), package) == 0


def rust_coverage_command(kind: str, report: Path) -> list[str]:
    if kind == "llvm-cov":
        return ["cargo", "llvm-cov", "--lcov", "--output-path", str(report)]
    return ["cargo", "tarpaulin", "--out", "Lcov", "--output-dir", str(report.parent)]


def _languages_in(files: list[Path]) -> set[str]:
    found = set()
    for file in files:
        language = language_of(file)
        if language:
            found.add(language)
    return found


def _modules_with(files: list[Path], suffix: str, marker: str, root: Path) -> list[Path]:
    modules: set[Path] = set()
    for file in files:
        if Path(file).suffix != suffix:
            continue
        module = nearest_marker(file, marker, root)
        if module is not None:
            modules.add(module)
    return sorted(modules)


def _cover_clojure(root: Path) -> None:
    if not (root / "deps.edn").is_file() and not (root / "bb.edn").is_file():
        _warn("No deps.edn or bb.edn; skipping Clojure coverage.")
        return
    _clean_clojure(root)
    code = run_shell(["clj", "-M:cov", "--lcov"], root)
    if code != 0:
        _warn("clj -M:cov --lcov failed; retrying without --lcov.")
        code = run_shell(["clj", "-M:cov"], root)
    if code != 0:
        _warn(f"Clojure coverage exited {code}. Clojure coverage will score 0%.")


def _cover_java(root: Path, files: list[Path]) -> None:
    modules = _modules_with(files, ".java", "pom.xml", root)
    if not modules:
        _warn("No pom.xml; skipping Java coverage.")
        return
    for module in modules:
        _clean_dir(module / "target" / "site" / "jacoco")
        exec_file = module / "target" / "jacoco.exec"
        if exec_file.exists():
            exec_file.unlink()
        code = run_shell(_MAVEN, module)
        if code != 0:
            _warn(f"Java coverage exited {code} in {module}. Java coverage will score 0%.")


def _cover_go(root: Path, files: list[Path]) -> None:
    modules = _modules_with(files, ".go", "go.mod", root)
    if not modules:
        _warn("No go.mod; skipping Go coverage.")
        return
    for module in modules:
        profile = module / "target" / "coverage" / "go" / "coverage.out"
        profile.parent.mkdir(parents=True, exist_ok=True)
        if profile.exists():
            profile.unlink()
        code = run_shell(["go", "test", "./...", f"-coverprofile={profile}"], module)
        if code != 0:
            _warn(f"Go coverage exited {code} in {module}. Go coverage will score 0%.")


def _prepare_report(report: Path) -> None:
    if report.parent.exists():
        shutil.rmtree(report.parent)
    report.parent.mkdir(parents=True, exist_ok=True)


def _cover_typescript(root: Path, files: list[Path]) -> None:
    packages = typescript_packages(root, files)
    if not packages:
        _warn("No package.json; skipping TypeScript coverage.")
        return
    for package in packages:
        report = _coverage_report(root, package, "typescript")
        try:
            command = typescript_command(package, files, report.parent)
        except PackageJsonError as exc:
            _warn(str(exc))
            continue
        if command is None:
            _warn(f"No package.json test script in {package}; skipping TypeScript coverage.")
            continue
        if _needs_vitest_provider(command) and not _ensure_vitest_coverage(package):
            continue
        _prepare_report(report)
        code = run_shell(command, package)
        if code != 0:
            _warn(f"TypeScript coverage exited {code} in {package}. TypeScript coverage will score 0%.")


def _needs_vitest_provider(command: list[str]) -> bool:
    if command[:3] == ["npm", "run", "coverage"]:
        return False
    return any(Path(part).name == "vitest" for part in command)


def _python_kind(
    py: str, package: Path, *, tests: str | None = None, installer: str = "pip"
) -> str:
    if not uses_pytest(package, tests):
        return "unittest"
    if _ensure_python_module(py, package, "pytest", installer):
        return "pytest"
    _warn(f"pytest is missing in {package}. Falling back to unittest.")
    return "unittest"


def _record_python_lcov(
    package: Path, code: int, data_file: Path, lcov_cmd: str | list[str]
) -> None:
    if not data_file.exists():
        if code != 0:
            _warn(f"Python coverage exited {code} in {package}. Python coverage will score 0%.")
        return
    lcov_code = run_shell(lcov_cmd, package)
    if lcov_code != 0:
        _warn(f"coverage lcov exited {lcov_code} in {package}. Python coverage will score 0%.")
    elif code != 0:
        _warn(f"Python tests exited {code} in {package}. Coverage was still recorded.")


def _combine_python_data(py: str, package: Path, data_file: Path) -> None:
    code = run_shell(python_combine_command(py, data_file), package)
    if code != 0:
        _warn(f"coverage combine exited {code} in {package}. Python coverage will score 0%.")


def _cover_python(
    root: Path,
    files: list[Path],
    *,
    tests: str | None = None,
    installer: str = "pip",
    subprocesses: bool = False,
) -> None:
    for package in python_roots(root, files, tests):
        report = _coverage_report(root, package, "python")
        py = _python_executable(package)
        if not _ensure_python_module(py, package, "coverage", installer):
            _warn(f"coverage is missing in {package}. Python coverage will score 0%.")
            continue
        report.parent.mkdir(parents=True, exist_ok=True)
        data_file = report.parent / ".coverage"
        report.unlink(missing_ok=True)
        data_file.unlink(missing_ok=True)
        rcfile = None
        if subprocesses:
            for stale in report.parent.glob(".coverage.*"):
                stale.unlink()
            usable, rcfile = _subprocess_rcfile(py, package, report.parent)
            if not usable:
                _warn(f"Python coverage will score 0% in {package}.")
                continue
        run_cmd, lcov_cmd = python_coverage_commands(
            py,
            _python_kind(py, package, tests=tests, installer=installer),
            data_file,
            report,
            python_sources(package, files),
            tests=tests,
            rcfile=rcfile,
        )
        code = run_shell(run_cmd, package)
        if subprocesses:
            _combine_python_data(py, package, data_file)
        _record_python_lcov(package, code, data_file, lcov_cmd)


def _cover_rust(root: Path, files: list[Path]) -> None:
    modules = rust_modules(root, files)
    if not modules:
        _warn("No Cargo.toml; skipping Rust coverage.")
        return
    kind = _rust_kind()
    if kind is None:
        return
    for module in modules:
        report = _coverage_report(root, module, "rust")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.unlink(missing_ok=True)
        code = run_shell(rust_coverage_command(kind, report), module)
        if code != 0:
            _warn(f"Rust coverage exited {code} in {module}. Rust coverage will score 0%.")


def run_coverage(
    root: Path,
    files: list[Path],
    command: str | None,
    *,
    python_tests: str | None = None,
    python_installer: str = "pip",
    python_subprocesses: bool = False,
) -> int:
    """Generate coverage reports for the languages present in `files`.

    `python_tests` names the Python test directory, relative to `root`.
    `python_installer` ("pip" or "uv") installs a missing `coverage` or `pytest`.
    `python_subprocesses` also measures Python child processes started by the tests.

    Returns the custom command's status. A non-zero status means the caller
    must not read reports already on disk. Per-language runs return 0; a
    failed tool is reported and that language scores 0% when it wrote nothing.
    """

    root = root.resolve()
    if command:
        code = run_shell(command, root)
        if code != 0:
            _warn(
                f"Coverage command exited {code}. "
                "Reports already on disk will not be read."
            )
        return code

    languages = _languages_in(files)
    if "clojure" in languages:
        _cover_clojure(root)
    if "java" in languages:
        _cover_java(root, files)
    if "go" in languages:
        _cover_go(root, files)
    if "typescript" in languages:
        _cover_typescript(root, files)
    if "python" in languages:
        _cover_python(
            root,
            files,
            tests=python_tests,
            installer=python_installer,
            subprocesses=python_subprocesses,
        )
    if "rust" in languages:
        _cover_rust(root, files)
    return 0
