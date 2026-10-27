"""Command-line entry point for ``furqan-lint``.

Subcommands:

* ``furqan-lint check <path>``           run the structural checks
  on a file or recursively on a directory of ``.py`` files.
* ``furqan-lint diff <old.py> <new.py>`` compare two versions of a
  module's public API and fire on removed names.
* ``furqan-lint version``                print the package version.
* ``furqan-lint --help``                 print usage.

Exit codes:

* ``0`` clean (no marads).
* ``1`` at least one marad fired.
* ``2`` a Python source file failed to parse.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        "node_modules",
        ".tox",
        ".mypy_cache",
        "target",
        "vendor",
        "cmd",
    }
)

# ---------------------------------------------------------------------------
# HABL integration state (Phase 2 -- Hydroponic Grow Plan)
# ---------------------------------------------------------------------------
# Set by main() when --habl is passed. Module-level so the check
# functions can read it without signature changes to the existing
# _check_file / _check_*_file call chain.
_habl_config: dict[str, object] = {
    "enabled": False,
    "project_root": None,
}

# Single source of truth for which file extensions the directory
# walker considers. Future adapters add their extension here, not
# in scattered string literals. Round-24 finding C2 closure: prior
# to v0.9.0 the walker had a hardcoded {".py", ".rs", ".go"} set
# at one site and a "No .py or .rs files found" error message at
# another (the message bug-stale since v0.8.0 when .go landed),
# which silently dropped .onnx files when the v0.9.0 adapter was
# added. The constant + derived error message close that gap.
_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".py", ".rs", ".go", ".onnx"})


def _parse_error_detail(exc: Exception) -> str:
    """Return a parse-error detail string with no redundant filename.

    The header line of a ``PARSE ERROR`` block already prints the path
    plus a ``(side, additive-only)`` qualifier; the indented detail line
    only needs the parser-internal location and kind. For
    :class:`RustParseError` we have structured ``kind``/``line``
    attributes, so we format ``"{kind} at line {line}"``. For
    :class:`GoParseError` (a plain ``Exception`` whose message is the
    goast binary's stderr) we return ``str(exc)`` unchanged.
    """
    kind = getattr(exc, "kind", None)
    line = getattr(exc, "line", None)
    if kind is not None and line is not None:
        return f"{kind} at line {line}"
    return str(exc)


def main() -> int:  # noqa: PLR0915
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        _print_usage()
        return 0

    if args[0] == "version":
        from furqan_lint import __version__

        print(f"furqan-lint {__version__}")
        return 0

    if args[0] == "check":
        # Phase G11.0 / T09: --gate11 flag enables CASM Gate 11
        # verification on bundles in the path. The flag is
        # parsed out before resolving the path argument.
        gate11_enabled = False
        # Phase 2 (Hydroponic Grow Plan): --habl flag enables HABL
        # finding emission. When set, every diagnostic is also
        # converted to a HABL Finding envelope and appended to
        # .habl/findings.jsonl in the project root.
        habl_enabled = False
        habl_project_root: str | None = None
        # Phase G11.1 audit C-1 + H-5 + H-6 corrective: include
        # the new identity-policy and force-refresh keys so the
        # dir-walker through cmd_check_gate11 can route them
        # through verify_bundle.
        gate11_opts: dict[str, object] = {
            "trust_config_path": None,
            "force_refresh": False,
            "expected_identity": None,
            "expected_issuer": None,
            "allow_any_identity": False,
            "use_placeholder_checker_hash": False,
        }
        check_args: list[str] = []
        i = 1
        while i < len(args):
            a = args[i]
            if a == "--habl":
                habl_enabled = True
                i += 1
            elif a == "--habl-root":
                if i + 1 >= len(args):
                    print(
                        "--habl-root requires a path argument",
                        file=sys.stderr,
                    )
                    return 2
                habl_project_root = args[i + 1]
                i += 2
            elif a == "--gate11":
                gate11_enabled = True
                i += 1
            elif a == "--trust-config":
                if i + 1 >= len(args):
                    print(
                        "--trust-config requires a path argument",
                        file=sys.stderr,
                    )
                    return 2
                gate11_opts["trust_config_path"] = Path(args[i + 1])
                i += 2
            elif a == "--force-refresh":
                gate11_opts["force_refresh"] = True
                i += 1
            elif a == "--expected-identity":
                if i + 1 >= len(args):
                    print(
                        "--expected-identity requires a pattern argument",
                        file=sys.stderr,
                    )
                    return 2
                gate11_opts["expected_identity"] = args[i + 1]
                i += 2
            elif a == "--expected-issuer":
                if i + 1 >= len(args):
                    print(
                        "--expected-issuer requires an issuer URL argument",
                        file=sys.stderr,
                    )
                    return 2
                gate11_opts["expected_issuer"] = args[i + 1]
                i += 2
            elif a == "--allow-any-identity":
                gate11_opts["allow_any_identity"] = True
                i += 1
            else:
                check_args.append(a)
                i += 1
        if not check_args:
            _print_usage(file=sys.stderr)
            return 1
        target = Path(check_args[0])
        if not target.exists():
            print(f"Not found: {target}", file=sys.stderr)
            return 1

        # Wire HABL config into module-level state so check functions
        # can emit findings without signature changes.
        if habl_enabled:
            _habl_config["enabled"] = True
            # Default project root: the target directory itself (or
            # its parent if it's a file). --habl-root overrides.
            if habl_project_root is not None:
                _habl_config["project_root"] = Path(habl_project_root)
            elif target.is_dir():
                _habl_config["project_root"] = target
            else:
                _habl_config["project_root"] = target.parent

        primary_exit = _check_file(target) if target.is_file() else _check_directory(target)
        if not gate11_enabled:
            return primary_exit
        # When --gate11 is set, also run gate11 verification on
        # any bundles in the directory tree. Use max(exit) so a
        # gate11 failure escalates the overall exit code.
        from furqan_lint.gate11.cli import cmd_check_gate11

        directory = target if target.is_dir() else target.parent
        gate11_exit = cmd_check_gate11(directory, gate11_opts)
        return max(primary_exit, gate11_exit)

    if args[0] == "diff" and len(args) >= 3:
        old_path = Path(args[1])
        new_path = Path(args[2])
        return _check_additive(old_path, new_path)

    if args[0] == "manifest":
        # Phase G11.0 / T09: Sigstore-CASM Gate 11 manifest
        # subcommand. Dispatched into furqan_lint.gate11.cli so
        # the [gate11] extra deps stay lazy on import.
        from furqan_lint.gate11.cli import dispatch_manifest

        return dispatch_manifest(args[1:])

    print(f"Unknown command: {args[0]}", file=sys.stderr)
    _print_usage(file=sys.stderr)
    return 1


def _print_usage(file: TextIO | None = None) -> None:
    out = file if file is not None else sys.stdout
    print("furqan-lint: structural-honesty checks for Python", file=out)
    print(file=out)
    print("Usage:", file=out)
    print("  furqan-lint check <file.py>", file=out)
    print("  furqan-lint check <directory/>", file=out)
    print("  furqan-lint check --habl <path>        (emit HABL findings)", file=out)
    print("  furqan-lint check --habl --habl-root <dir> <path>", file=out)
    print("  furqan-lint check --gate11 <path>", file=out)
    print("  furqan-lint diff <old.py> <new.py>", file=out)
    print("  furqan-lint manifest init <module.py> [--trust-config PATH]", file=out)
    print(
        "  furqan-lint manifest verify <bundle.sigstore> [--trust-config PATH] [--force-refresh]",
        file=out,
    )
    print("  furqan-lint manifest update <module.py> [--trust-config PATH]", file=out)
    print("  furqan-lint version", file=out)


def _check_file(path: Path) -> int:
    """Dispatch a check by file suffix.

    .py -> existing Python adapter pipeline.
    .rs -> Rust adapter (opt-in via [rust] extra). If
    tree-sitter is not installed, prints an install hint to stderr
    and exits 1 (not 2; not-installed is a configuration issue,
    not a parse failure).

    Any other suffix is treated as Python (back-compat with v0.6.x;
    callers who pass a .py.bak or similar still get the old behaviour).
    """
    if path.suffix == ".rs":
        return _check_rust_file(path)
    if path.suffix == ".go":
        return _check_go_file(path)
    if path.suffix == ".onnx":
        return _check_onnx_file(path)
    return _check_python_file(path)


def _check_python_file(path: Path) -> int:
    import ast as _ast

    from furqan.errors.marad import Advisory, Marad

    from furqan_lint.adapter import translate_tree
    from furqan_lint.runner import check_python_module
    from furqan_lint.zero_return import ZeroReturnDiagnostic

    try:
        source = path.read_text(encoding="utf-8")
        tree = _ast.parse(source, filename=str(path))
        module = translate_tree(tree, str(path))
    except SyntaxError as e:
        line = e.lineno if e.lineno is not None else 0
        print(f"SYNTAX ERROR  {path}:{line}")
        print(f"  {e.msg}")
        return 2

    diagnostics = check_python_module(module, source_tree=tree)
    marads = [(n, d) for n, d in diagnostics if isinstance(d, Marad)]
    advisories = [(n, d) for n, d in diagnostics if isinstance(d, Advisory)]
    r3_diags = [(n, d) for n, d in diagnostics if isinstance(d, ZeroReturnDiagnostic)]
    is_failure = bool(marads or r3_diags)

    if not diagnostics:
        print(f"PASS  {path}")
        print("  4 structural checks ran. Zero diagnostics.")
        return 0

    if marads:
        print(f"MARAD  {path}")
        print(f"  {len(marads)} violation(s):")
        for name, m in marads:
            print(f"    [{name}] {m.diagnosis}")
            fix = getattr(m, "minimal_fix", None)
            if fix:
                print(f"      fix: {fix}")
        print()

    if r3_diags:
        print(f"MARAD  {path}")
        print(f"  {len(r3_diags)} ring-close violation(s):")
        for name, d in r3_diags:
            print(f"    [{name}] {d.diagnosis}")
        print()

    if advisories:
        print(f"ADVISORY  {path}")
        print(f"  {len(advisories)} note(s):")
        for name, a in advisories:
            msg = getattr(a, "message", str(a))
            print(f"    [{name}] {msg}")

    # HABL emission (Phase 2 -- the mycorrhizal tap root)
    if _habl_config["enabled"] and diagnostics:
        from furqan_lint.habl_emitter import emit_to_ledger
        count = emit_to_ledger(diagnostics, str(path), _habl_config["project_root"])
        if count:
            print(f"  HABL: {count} finding(s) appended to ledger")

    return 1 if is_failure else 0


def _check_rust_file(path: Path) -> int:
    """Lint a single .rs file using the Rust adapter.

    Runs three checkers: R3 (zero-return via upstream
    ``furqan.checker.check_ring_close``), D24 (all-paths-return),
    and D11 (status-coverage with Option- AND Result-aware
    producer predicate). The Rust analogue of return_none_mismatch
    was dropped per the v0.7.2 prompt-grounding self-check; see
    rust_adapter/runner.py docstring for the rationale.
    """
    try:
        from furqan_lint.rust_adapter import (
            RustExtrasNotInstalled,
            RustParseError,
        )
        from furqan_lint.rust_adapter import parse_file as parse_rust
    except ImportError:
        print(
            "Rust support not installed. Run: pip install furqan-lint[rust]",
            file=sys.stderr,
        )
        return 1

    from furqan.errors.marad import Advisory, Marad

    try:
        module = parse_rust(path)
    except RustExtrasNotInstalled as e:
        # The package itself imported, but tree_sitter / tree_sitter_rust
        # could not be imported when parse_file probed for them. Print
        # the typed exception's message (the install hint) rather than
        # dumping a Python traceback.
        print(str(e), file=sys.stderr)
        return 1
    except RustParseError as e:
        print(f"PARSE ERROR  {path}:{e.line}")
        print(f"  {e.kind}")
        return 2

    # Rust pipeline: R3 + D24 + D11 via the Rust runner (current as of v0.7.2). The
    # runner wires upstream check_ring_close (filtered to R3-shaped
    # diagnostics), check_all_paths_return (D24), and
    # check_status_coverage (D11) in the order R3 -> D24 -> D11.
    from furqan_lint.rust_adapter.runner import check_rust_module

    diagnostics = check_rust_module(module)
    marads = [(n, d) for n, d in diagnostics if isinstance(d, Marad)]
    advisories = [(n, d) for n, d in diagnostics if isinstance(d, Advisory)]

    if not diagnostics:
        print(f"PASS  {path}")
        print(
            "  3 structural checks ran (R3, D24, D11 with Option- and Result-aware status coverage). Zero diagnostics."
        )
        return 0

    if marads:
        print(f"MARAD  {path}")
        print(f"  {len(marads)} violation(s):")
        for name, m in marads:
            print(f"    [{name}] {m.diagnosis}")
            fix = getattr(m, "minimal_fix", None)
            if fix:
                print(f"      fix: {fix}")
        print()

    if advisories:
        print(f"ADVISORY  {path}")
        print(f"  {len(advisories)} note(s):")
        for name, a in advisories:
            msg = getattr(a, "message", str(a))
            print(f"    [{name}] {msg}")

    # HABL emission (Phase 2 -- the mycorrhizal tap root)
    if _habl_config["enabled"] and diagnostics:
        from furqan_lint.habl_emitter import emit_to_ledger
        count = emit_to_ledger(diagnostics, str(path), _habl_config["project_root"])
        if count:
            print(f"  HABL: {count} finding(s) appended to ledger")

    return 1 if marads else 0


def _check_go_file(path: Path) -> int:
    """Lint a single .go file using the v0.8.0 Go adapter.

    Runs three checkers: D24 (all-paths-return) + D11 (status-
    coverage with the (T, error) firing shape). R3 is not wired
    for Go in v0.8.0; deferred until a Go-specific zero-return
    body shape motivates the design.

    Mirrors the v0.7.0.1 Rust pattern: typed
    ``GoExtrasNotInstalled`` exception is raised by parse_file
    when the bundled goast binary is missing; the CLI catches it
    and emits the install hint to stderr with exit 1 (NOT a
    Python traceback).
    """
    try:
        from furqan_lint.go_adapter import (
            GoExtrasNotInstalled,
            GoParseError,
        )
        from furqan_lint.go_adapter import parse_file as parse_go
    except ImportError:
        print(
            "Go support not installed. Run: pip install furqan-lint[go]",
            file=sys.stderr,
        )
        return 1

    from furqan.errors.marad import Advisory, Marad

    try:
        from furqan_lint.go_adapter.runner import check_go_module
        from furqan_lint.go_adapter.translator import translate

        data = parse_go(path)
        module = translate(data, filename=str(path))
    except GoExtrasNotInstalled as e:
        # The package itself imported, but the bundled goast
        # binary is missing. Print the typed exception's message
        # (the install hint) rather than dumping a traceback.
        print(str(e), file=sys.stderr)
        return 1
    except GoParseError as e:
        print(f"PARSE ERROR  {path}")
        print(f"  {e}")
        return 2

    diagnostics = check_go_module(module)
    marads = [(n, d) for n, d in diagnostics if isinstance(d, Marad)]
    advisories = [(n, d) for n, d in diagnostics if isinstance(d, Advisory)]

    if not diagnostics:
        print(f"PASS  {path}")
        print("  2 structural checks ran (D24, D11 with (T, error) firing). Zero diagnostics.")
        return 0

    if marads:
        print(f"MARAD  {path}")
        print(f"  {len(marads)} violation(s):")
        for name, m in marads:
            print(f"    [{name}] {m.diagnosis}")
            fix = getattr(m, "minimal_fix", None)
            if fix:
                print(f"      fix: {fix}")
        print()

    if advisories:
        print(f"ADVISORY  {path}")
        print(f"  {len(advisories)} note(s):")
        for name, a in advisories:
            msg = getattr(a, "message", str(a))
            print(f"    [{name}] {msg}")

    # HABL emission (Phase 2 -- the mycorrhizal tap root)
    if _habl_config["enabled"] and diagnostics:
        from furqan_lint.habl_emitter import emit_to_ledger
        count = emit_to_ledger(diagnostics, str(path), _habl_config["project_root"])
        if count:
            print(f"  HABL: {count} finding(s) appended to ledger")

    return 1 if marads else 0


def _check_onnx_file(path: Path) -> int:
    """Lint a single .onnx file using the v0.9.0 ONNX adapter.

    Runs the ONNX checker pipeline (D24-onnx all-paths-emit +
    opset-compliance with the pinned op registry + D11-onnx
    shape-coverage via strict-mode shape inference). D11-onnx
    landed in v0.9.1 per Decision 1 of that prompt; the call
    site passes ``model`` (the ModelProto returned by
    ``parse_model``) to ``check_onnx_module`` so strict-mode
    inference can run on the protobuf directly.

    Typed ``OnnxExtrasNotInstalled`` is raised by parse_model
    when the [onnx] extra is missing; the CLI catches it and
    emits the install hint to stderr with exit 1 (NOT a Python
    traceback). ``OnnxParseError`` maps to exit 2.
    """
    try:
        from furqan_lint.onnx_adapter import (
            OnnxExtrasNotInstalled,
            OnnxParseError,
            parse_model,
        )
        from furqan_lint.onnx_adapter.numpy_divergence import (
            NumpyDivergenceDiagnostic,
        )
        from furqan_lint.onnx_adapter.runner import (
            AllPathsEmitDiagnostic,
            OpsetComplianceDiagnostic,
            check_onnx_module,
        )
        from furqan_lint.onnx_adapter.score_validity import (
            ScoreValidityDiagnostic,
        )
        from furqan_lint.onnx_adapter.shape_coverage import (
            ShapeCoverageDiagnostic,
        )
        from furqan_lint.onnx_adapter.translator import to_onnx_module
    except ImportError:
        print(
            "ONNX support not installed. Run: pip install furqan-lint[onnx]",
            file=sys.stderr,
        )
        return 1

    try:
        model = parse_model(path)
    except OnnxExtrasNotInstalled as e:
        print(str(e), file=sys.stderr)
        return 1
    except OnnxParseError as e:
        print(f"PARSE ERROR  {path}")
        print(f"  {e.detail}")
        return 2

    module = to_onnx_module(model)
    diagnostics = check_onnx_module(module, model, path)

    if not diagnostics:
        print(f"PASS  {path}")
        print(
            "  5 structural checks ran "
            "(D24-onnx all-paths-emit, opset-compliance, "
            "D11-onnx shape-coverage, numpy_divergence, "
            "score_validity). Zero diagnostics. "
            "(numpy_divergence silent-passes when [onnx-runtime] "
            "is missing or no NeuroGolf-convention sidecar is "
            "present; score_validity silent-passes when "
            "[onnx-profile] is missing.)"
        )
        return 0

    # v0.9.4 split: ADVISORY findings exit 0; MARAD findings exit 1.
    # ScoreValidityDiagnostic.severity is "ADVISORY" in v0.9.4; the
    # other four diagnostic families do not carry the field, so
    # getattr(d, "severity", "MARAD") defaults them to MARAD.
    advisory = []
    marad = []
    for name, d in diagnostics:
        if isinstance(
            d,
            AllPathsEmitDiagnostic
            | OpsetComplianceDiagnostic
            | ShapeCoverageDiagnostic
            | NumpyDivergenceDiagnostic
            | ScoreValidityDiagnostic,
        ):
            severity = getattr(d, "severity", "MARAD")
            if severity == "ADVISORY":
                advisory.append((name, d))
            else:
                marad.append((name, d))

    if marad and advisory:
        print(f"MARAD  {path}")
        print(f"  5 structural checks ran, " f"{len(marad)} MARAD, {len(advisory)} ADVISORY:")
    elif marad:
        print(f"MARAD  {path}")
        print(f"  {len(marad)} violation(s):")
    else:
        print(f"ADVISORY  {path}")
        print(f"  {len(advisory)} advisory finding(s):")

    for name, d in marad:
        print(f"    [MARAD] [{name}] {d.diagnosis}")
        # Part 5b(a): print minimal_fix matching Python/Rust/Go pattern.
        fix = getattr(d, "minimal_fix", None)
        if fix:
            print(f"      fix: {fix}")
    for name, d in advisory:
        print(f"    [ADVISORY] [{name}] {d.diagnosis}")
        fix = getattr(d, "minimal_fix", None)
        if fix:
            print(f"      fix: {fix}")

    # HABL emission (Phase 2 -- the mycorrhizal tap root)
    if _habl_config["enabled"] and diagnostics:
        from furqan_lint.habl_emitter import emit_to_ledger
        count = emit_to_ledger(diagnostics, str(path), _habl_config["project_root"])
        if count:
            print(f"  HABL: {count} finding(s) appended to ledger")

    # Decision 3: ADVISORY exits 0; MARAD exits 1.
    return 1 if marad else 0


def _check_additive(old_path: Path, new_path: Path) -> int:
    """Dispatch the additive-only diff to the language-appropriate
    helper based on file suffix.

    Guard ordering (load-bearing per locked decision 4):

    1. Cross-language pairs (suffix mismatch) return exit 2 with
       a "Cross-language diff not supported" message. MUST be
       evaluated FIRST so a ``foo.py`` vs ``bar.rs`` pair says
       "cross-language", not the language-specific verdict.
    2. ``.rs`` vs ``.rs`` pairs route to ``_check_rust_additive``
       (added in v0.8.2 commit 2; replaced the v0.8.1
       not-implemented guard).
    3. ``.go`` vs ``.go`` pairs route to ``_check_go_additive``
       (added in v0.8.1 commit 2).
    4. Default: ``_check_python_additive`` (preserved verbatim
       from v0.8.0's monolithic body).
    """
    # Guard 1: cross-language rejection (MUST BE FIRST).
    if old_path.suffix != new_path.suffix:
        print(f"PARSE ERROR  {new_path} (additive-only)")
        print(
            f"  Cross-language diff not supported. "
            f"Old: '{old_path.suffix}'; new: '{new_path.suffix}'."
        )
        return 2

    # Guard 2: Rust diff (added in v0.8.2 commit 2).
    if old_path.suffix == ".rs":
        return _check_rust_additive(old_path, new_path)

    # Guard 3: Go diff (added in v0.8.1 commit 2).
    if old_path.suffix == ".go":
        return _check_go_additive(old_path, new_path)

    # Guard 4: ONNX diff (added in v0.9.0).
    if old_path.suffix == ".onnx":
        return _check_onnx_additive(old_path, new_path)

    # Default: Python diff.
    return _check_python_additive(old_path, new_path)


def _check_python_additive(old_path: Path, new_path: Path) -> int:
    """Python additive-only diff via :func:`check_additive_api`.

    Body lifted verbatim from v0.8.0's monolithic ``_check_additive``
    minus the Go-rejection guard (now handled by the dispatcher).
    All Python diff tests pass unchanged after the v0.8.1 refactor.
    """
    from furqan_lint.additive import DynamicAllError, check_additive_api

    try:
        old_source = old_path.read_text(encoding="utf-8")
        new_source = new_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"Cannot read source: {e}", file=sys.stderr)
        return 1

    try:
        diagnostics = check_additive_api(new_source, old_source, filename=str(new_path))
    except SyntaxError as e:
        line = e.lineno if e.lineno is not None else 0
        print(f"SYNTAX ERROR  {e.filename or new_path}:{line}")
        print(f"  {e.msg}")
        return 2
    except DynamicAllError as e:
        path = new_path if e.where == "new" else old_path
        print(f"INDETERMINATE  {path} (additive-only)")
        print(f"  {e}")
        print(
            "  Refusing to certify a public surface that cannot be "
            "statically read. Replace the dynamic __all__ assignment "
            "with a literal list or tuple of string literals."
        )
        return 2

    if not diagnostics:
        print(f"PASS  {new_path} (additive-only)")
        print("  No public names removed.")
        return 0

    print(f"MARAD  {new_path} (additive-only)")
    print(f"  {len(diagnostics)} violation(s):")
    for m in diagnostics:
        print(f"    [additive_only] {m.diagnosis}")
        print(f"      fix: {m.minimal_fix}")
    return 1


def _check_rust_additive(old_path: Path, new_path: Path) -> int:
    """Rust additive-only diff via :func:`compare_name_sets` plus
    :func:`extract_public_names` from each ``.rs`` file.

    Catches ``RustExtrasNotInstalled`` (install hint, exit 1) and
    ``RustParseError`` (exit 2) per the v0.7.0.1 typed-exception
    pattern: the user sees a one-line message, not a Python
    traceback. Mirrors :func:`_check_go_additive` exactly minus
    the language tag.
    """
    try:
        from furqan_lint.additive import compare_name_sets
        from furqan_lint.rust_adapter import (
            RustExtrasNotInstalled,
            RustParseError,
            extract_public_names,
        )
    except ImportError:
        print(
            "Rust support not installed. Run: pip install furqan-lint[rust]",
            file=sys.stderr,
        )
        return 1

    try:
        try:
            old_names = extract_public_names(old_path)
        except RustParseError as e:
            print(f"PARSE ERROR  {old_path}  (old side, additive-only)")
            print(f"  {_parse_error_detail(e)}")
            return 2
        try:
            new_names = extract_public_names(new_path)
        except RustParseError as e:
            print(f"PARSE ERROR  {new_path}  (new side, additive-only)")
            print(f"  {_parse_error_detail(e)}")
            return 2
    except RustExtrasNotInstalled as e:
        print(str(e), file=sys.stderr)
        return 1

    diagnostics = compare_name_sets(
        previous_names=old_names,
        current_names=new_names,
        filename=str(new_path),
        language="rust",
    )

    if not diagnostics:
        print(f"PASS  {new_path} (additive-only)")
        print("  No public names removed.")
        return 0

    print(f"MARAD  {new_path} (additive-only)")
    print(f"  {len(diagnostics)} violation(s):")
    for m in diagnostics:
        print(f"    [additive_only] {m.diagnosis}")
        print(f"      fix: {m.minimal_fix}")
    return 1


def _check_go_additive(old_path: Path, new_path: Path) -> int:
    """Go additive-only diff via :func:`compare_name_sets` plus
    :func:`extract_public_names` from each ``.go`` file.

    Catches ``GoExtrasNotInstalled`` (install hint, exit 1) and
    ``GoParseError`` (exit 2) per the v0.7.0.1 typed-exception
    pattern: the user sees a one-line message, not a Python
    traceback.
    """
    try:
        from furqan_lint.additive import compare_name_sets
        from furqan_lint.go_adapter import (
            GoExtrasNotInstalled,
            GoParseError,
            extract_public_names,
        )
    except ImportError:
        print(
            "Go support not installed. Run: pip install furqan-lint[go]",
            file=sys.stderr,
        )
        return 1

    try:
        try:
            old_names = extract_public_names(old_path)
        except GoParseError as e:
            print(f"PARSE ERROR  {old_path}  (old side, additive-only)")
            print(f"  {_parse_error_detail(e)}")
            return 2
        try:
            new_names = extract_public_names(new_path)
        except GoParseError as e:
            print(f"PARSE ERROR  {new_path}  (new side, additive-only)")
            print(f"  {_parse_error_detail(e)}")
            return 2
    except GoExtrasNotInstalled as e:
        print(str(e), file=sys.stderr)
        return 1

    diagnostics = compare_name_sets(
        previous_names=old_names,
        current_names=new_names,
        filename=str(new_path),
        language="go",
    )

    if not diagnostics:
        print(f"PASS  {new_path} (additive-only)")
        print("  No public names removed.")
        return 0

    print(f"MARAD  {new_path} (additive-only)")
    print(f"  {len(diagnostics)} violation(s):")
    for m in diagnostics:
        print(f"    [additive_only] {m.diagnosis}")
        print(f"      fix: {m.minimal_fix}")
    return 1


def _check_onnx_additive(old_path: Path, new_path: Path) -> int:
    """ONNX additive-only diff via :func:`compare_name_sets` plus
    :func:`extract_public_names` from each ``.onnx`` file.

    Catches ``OnnxExtrasNotInstalled`` (install hint, exit 1) and
    ``OnnxParseError`` (exit 2) per the typed-exception pattern
    shared with the Rust and Go adapters: the user sees a one-line
    message, not a Python traceback. Public names cover only
    ``graph.input`` and ``graph.output`` ValueInfo entries with
    their shapes (Decision 5 of the v0.9.0 prompt); intermediates
    and initializers are out of scope.
    """
    try:
        from furqan_lint.additive import compare_name_sets
        from furqan_lint.onnx_adapter import (
            OnnxExtrasNotInstalled,
            OnnxParseError,
            extract_public_names,
        )
    except ImportError:
        print(
            "ONNX support not installed. Run: pip install furqan-lint[onnx]",
            file=sys.stderr,
        )
        return 1

    try:
        try:
            old_names = extract_public_names(old_path)
        except OnnxParseError as e:
            print(f"PARSE ERROR  {old_path}  (old side, additive-only)")
            print(f"  {e.detail}")
            return 2
        try:
            new_names = extract_public_names(new_path)
        except OnnxParseError as e:
            print(f"PARSE ERROR  {new_path}  (new side, additive-only)")
            print(f"  {e.detail}")
            return 2
    except OnnxExtrasNotInstalled as e:
        print(str(e), file=sys.stderr)
        return 1

    diagnostics = compare_name_sets(
        previous_names=old_names,
        current_names=new_names,
        filename=str(new_path),
        language="onnx",
    )

    if not diagnostics:
        print(f"PASS  {new_path} (additive-only)")
        print("  No public names removed.")
        return 0

    print(f"MARAD  {new_path} (additive-only)")
    print(f"  {len(diagnostics)} violation(s):")
    for m in diagnostics:
        print(f"    [additive_only] {m.diagnosis}")
        print(f"      fix: {m.minimal_fix}")
    return 1


def _check_directory(directory: Path) -> int:
    exit_code = 0
    files = sorted(
        p
        for p in directory.rglob("*")
        if p.is_file()
        and p.suffix in _SUPPORTED_EXTENSIONS
        and not any(part in EXCLUDED_DIRS for part in p.parts)
    )
    if not files:
        extensions_str = ", ".join(sorted(_SUPPORTED_EXTENSIONS))
        print(f"No supported files found ({extensions_str}) in {directory}")
        return 0
    for path in files:
        result = _check_file(path)
        exit_code = max(exit_code, result)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
