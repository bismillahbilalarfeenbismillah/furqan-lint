# Bismillah ir-Rahman ir-Rahim
"""HABL Finding emitter for furqan-lint.

Converts furqan-lint diagnostics (Marad, Advisory, ZeroReturnDiagnostic)
into HABL Finding envelopes and appends them to the project ledger.

This is the mycorrhizal tap root -- the point where furqan-lint's
structural-honesty findings enter the shared connective tissue so that
FATIMA, Bayyinah, and HABL itself can correlate, respond, and grow.

Phase 2, Step 2 of the Hydroponic Grow Plan.

Requires: habl >= 0.1.0 (optional dependency of furqan-lint).
"""
# Bismillah ir-Rahman ir-Rahim

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Lazy import gate -- habl is an optional dependency
# ---------------------------------------------------------------------------

_habl_available: bool | None = None


def _check_habl() -> bool:
    """Return True iff the habl package is importable."""
    global _habl_available  # noqa: PLW0603
    if _habl_available is None:
        try:
            import habl  # noqa: F401
            _habl_available = True
        except ImportError:
            _habl_available = False
    return _habl_available


# ---------------------------------------------------------------------------
# Checker-name -> HABL primitive mapping
# ---------------------------------------------------------------------------

# Maps the checker_name from runner.py's (checker_name, diagnostic) tuples
# to a human-readable primitive tag for the HABL payload.
_CHECKER_PRIMITIVES: dict[str, str] = {
    "all_paths_return": "D24",
    "status_coverage": "D11",
    "return_none_mismatch": "return_none_mismatch",
    "zero_return_path": "R3",
}


# ---------------------------------------------------------------------------
# Conversion -- the three diagnostic shapes
# ---------------------------------------------------------------------------

def _convert_marad(
    checker_name: str,
    diag: object,
    file_path: str,
) -> dict:
    """Convert a Marad diagnostic into HABL Finding constructor kwargs.

    Marad fields:
        primitive: str
        diagnosis: str
        location: SourceSpan (file, line, column)
        minimal_fix: str
        regression_check: str
    """
    from habl import Category, Finding, Location, Severity

    loc = getattr(diag, "location", None)
    line = getattr(loc, "line", None) if loc else None
    fn_name = None

    # Extract function name from diagnosis text if present
    diagnosis = getattr(diag, "diagnosis", "")
    if "'" in diagnosis:
        # Pattern: "Function 'name' at line ..."
        parts = diagnosis.split("'")
        if len(parts) >= 2:
            fn_name = parts[1]

    return Finding(
        source_tool="furqan-lint",
        severity=Severity.HIGH,
        category=Category.STRUCTURAL,
        summary=diagnosis,
        location=Location(
            file=file_path,
            line=line,
            function=fn_name,
        ),
        payload={
            "primitive": getattr(diag, "primitive", _CHECKER_PRIMITIVES.get(checker_name, checker_name)),
            "checker": checker_name,
            "minimal_fix": getattr(diag, "minimal_fix", None),
            "regression_check": getattr(diag, "regression_check", None),
        },
    )


def _convert_advisory(
    checker_name: str,
    diag: object,
    file_path: str,
) -> dict:
    """Convert an Advisory diagnostic into a HABL Finding.

    Advisory fields:
        primitive: str
        message: str
        location: SourceSpan (file, line, column)
        suggestion: str
    """
    from habl import Category, Finding, Location, Severity

    loc = getattr(diag, "location", None)
    line = getattr(loc, "line", None) if loc else None

    return Finding(
        source_tool="furqan-lint",
        severity=Severity.ADVISORY,
        category=Category.STRUCTURAL,
        summary=getattr(diag, "message", ""),
        location=Location(
            file=file_path,
            line=line,
        ),
        payload={
            "primitive": getattr(diag, "primitive", _CHECKER_PRIMITIVES.get(checker_name, checker_name)),
            "checker": checker_name,
            "suggestion": getattr(diag, "suggestion", None),
        },
    )


def _convert_zero_return(
    checker_name: str,
    diag: object,
    file_path: str,
) -> dict:
    """Convert a ZeroReturnDiagnostic into a HABL Finding.

    ZeroReturnDiagnostic fields:
        function_name: str
        lineno: int
        col_offset: int
        declared_return: str
        diagnosis: str (property)
    """
    from habl import Category, Finding, Location, Severity

    return Finding(
        source_tool="furqan-lint",
        severity=Severity.HIGH,
        category=Category.STRUCTURAL,
        summary=getattr(diag, "diagnosis", ""),
        location=Location(
            file=file_path,
            line=getattr(diag, "lineno", None),
            function=getattr(diag, "function_name", None),
        ),
        payload={
            "primitive": "R3",
            "checker": checker_name,
            "declared_return": getattr(diag, "declared_return", None),
        },
    )


# ---------------------------------------------------------------------------
# Public API -- the tap root
# ---------------------------------------------------------------------------

def diagnostics_to_findings(
    diagnostics: list[tuple[str, object]],
    file_path: str,
) -> list:
    """Convert a list of furqan-lint (checker_name, diagnostic) tuples
    into HABL Finding envelopes.

    This is the main conversion entry point. Call it after
    check_python_module() or any language adapter returns its
    diagnostics list.

    Returns an empty list if habl is not installed.
    """
    if not _check_habl():
        return []

    from furqan.errors.marad import Advisory, Marad
    from furqan_lint.zero_return import ZeroReturnDiagnostic

    findings = []
    for checker_name, diag in diagnostics:
        if isinstance(diag, Marad):
            findings.append(_convert_marad(checker_name, diag, file_path))
        elif isinstance(diag, Advisory):
            findings.append(_convert_advisory(checker_name, diag, file_path))
        elif isinstance(diag, ZeroReturnDiagnostic):
            findings.append(_convert_zero_return(checker_name, diag, file_path))
        else:
            # Unknown diagnostic type -- emit as advisory with raw repr
            from habl import Category, Finding, Location, Severity
            findings.append(Finding(
                source_tool="furqan-lint",
                severity=Severity.ADVISORY,
                category=Category.STRUCTURAL,
                summary=str(diag),
                location=Location(file=file_path),
                payload={
                    "primitive": _CHECKER_PRIMITIVES.get(checker_name, checker_name),
                    "checker": checker_name,
                    "raw_type": type(diag).__name__,
                },
            ))

    return findings


def emit_to_ledger(
    diagnostics: list[tuple[str, object]],
    file_path: str,
    project_root: str | Path,
) -> int:
    """Convert diagnostics and append them to the HABL ledger.

    Returns the number of findings appended.
    Returns 0 if habl is not installed.
    """
    if not _check_habl():
        return 0

    from habl import append_findings as habl_append

    findings = diagnostics_to_findings(diagnostics, file_path)
    if findings:
        habl_append(findings, project_root)
    return len(findings)


# Bismillah ir-Rahman ir-Rahim
