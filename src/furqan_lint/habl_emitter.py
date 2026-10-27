# Bismillah ir-Rahman ir-Rahim
"""HABL Finding emitter for furqan-lint.

Converts furqan-lint diagnostics into HABL Finding envelopes and
appends them to the project ledger (``.habl/findings.jsonl``).

Contract (design review by GPT-6 of the first version, 3f6e80b):

1. Explicit export fails closed. If HABL is missing, incompatible, or
   the append fails, the caller gets a :class:`HablExportError`, never
   a silent zero-finding success.
2. Every diagnostic type is converted by a declared converter. A type
   with no converter raises :class:`UnsupportedDiagnosticError` before
   anything is written; it is never guessed into a severity.
3. Locations come only from structured fields. Nothing is parsed out of
   diagnostic prose.
4. A file's findings are built and validated first, then appended in
   one locked batch. HABL v0.1.1 is not crash-atomic, so a crash during
   the write can still leave a partial final record.

Requires: habl >= 0.1.1, < 0.2 (optional dependency of furqan-lint).
"""
# Bismillah ir-Rahman ir-Rahim

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from furqan.errors.marad import Advisory, Marad

from furqan_lint.zero_return import ZeroReturnDiagnostic

if TYPE_CHECKING:
    from habl import Finding

SOURCE_TOOL = "furqan-lint"

# The HABL API this emitter was written and tested against. v0.2 is
# expected to change the schema (canonical bytes, hash chain), so it
# must be adopted deliberately, not picked up by accident.
HABL_MIN_VERSION = (0, 1, 1)
HABL_MAX_VERSION_EXCLUSIVE = (0, 2, 0)
_HABL_REQUIRED_NAMES = (
    "Category",
    "Finding",
    "Location",
    "Severity",
    "append_findings",
)

# checker_name -> primitive tag, used when a diagnostic does not carry
# its own ``primitive`` field.
_CHECKER_PRIMITIVES: dict[str, str] = {
    "all_paths_return": "D24",
    "status_coverage": "D11",
    "return_none_mismatch": "return_none_mismatch",
    "zero_return_path": "R3",
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class HablExportError(RuntimeError):
    """HABL export was requested and did not complete."""


class HablUnavailableError(HablExportError):
    """HABL is not installed, or its version or API is incompatible."""


class UnsupportedDiagnosticError(HablExportError):
    """A diagnostic type has no declared converter.

    Raised before any finding from the batch is written, so the ledger
    is not left with a partial batch for that file.
    """

    def __init__(self, checker_name: str, diagnostic_type: str) -> None:
        self.checker_name = checker_name
        self.diagnostic_type = diagnostic_type
        super().__init__(
            f"no HABL converter for diagnostic type {diagnostic_type!r} "
            f"from checker {checker_name!r}; nothing was written for this file"
        )


# ---------------------------------------------------------------------------
# HABL availability and compatibility
# ---------------------------------------------------------------------------


def _parse_version(text: str) -> tuple[int, ...]:
    parts = []
    for piece in text.split(".")[:3]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def require_habl() -> ModuleType:
    """Import HABL and confirm it is a compatible version and API.

    Raises :class:`HablUnavailableError` with the reason otherwise.
    """
    try:
        import habl
    except ImportError as exc:
        msg = "HABL export requested but habl is not installed (pip install 'furqan-lint[habl]')"
        raise HablUnavailableError(msg) from exc

    raw = getattr(habl, "__version__", None)
    version = _parse_version(raw) if isinstance(raw, str) else ()
    if not version or not (HABL_MIN_VERSION <= version < HABL_MAX_VERSION_EXCLUSIVE):
        low = ".".join(map(str, HABL_MIN_VERSION))
        high = ".".join(map(str, HABL_MAX_VERSION_EXCLUSIVE))
        msg = f"habl {raw!r} is not supported; this emitter needs habl >= {low}, < {high}"
        raise HablUnavailableError(msg)

    missing = [n for n in _HABL_REQUIRED_NAMES if not hasattr(habl, n)]
    if missing:
        msg = f"habl {raw} is missing required API: {', '.join(missing)}"
        raise HablUnavailableError(msg)
    module: ModuleType = habl
    return module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ledger_file_path(file_path: str | Path, project_root: str | Path) -> str:
    """Path recorded in the finding: relative to the project root, in
    POSIX form, when the file is inside it; otherwise as given.

    A shared convention lets HABL correlate findings from different
    tools on the same file.
    """
    try:
        rel = Path(file_path).resolve().relative_to(Path(project_root).resolve())
    except ValueError:
        return str(file_path)
    return rel.as_posix()


def _location(
    habl: ModuleType, file_path: str, line: object = None, function: object = None
) -> Any:
    """Build a HABL Location from structured values only.

    A value of the wrong type is left unset rather than coerced.
    """
    ok_line = isinstance(line, int) and not isinstance(line, bool)
    return habl.Location(
        file=file_path,
        line=line if ok_line else None,
        function=function if isinstance(function, str) and function else None,
    )


def _span(diag: object) -> object:
    """The ``line`` of a diagnostic's SourceSpan, or None if absent."""
    return getattr(getattr(diag, "location", None), "line", None)


def _primitive(checker_name: str, diag: object) -> str:
    own = getattr(diag, "primitive", None)
    if isinstance(own, str) and own:
        return own
    return _CHECKER_PRIMITIVES.get(checker_name, checker_name)


def _base_payload(checker_name: str, diag: object) -> dict[str, Any]:
    return {
        "primitive": _primitive(checker_name, diag),
        "checker": checker_name,
        "diagnostic_type": type(diag).__name__,
    }


# ---------------------------------------------------------------------------
# Converters -- one per declared diagnostic type
# ---------------------------------------------------------------------------

_Converter = Callable[[ModuleType, str, Any, str], "Finding"]


def _convert_marad(habl: ModuleType, checker_name: str, diag: Any, file_path: str) -> Finding:
    """Marad: primitive, diagnosis, location (SourceSpan), minimal_fix,
    regression_check. A MARAD is a structural-honesty violation."""
    payload = _base_payload(checker_name, diag)
    payload["minimal_fix"] = diag.minimal_fix
    payload["regression_check"] = diag.regression_check
    return habl.Finding(
        source_tool=SOURCE_TOOL,
        severity=habl.Severity.HIGH,
        category=habl.Category.STRUCTURAL,
        summary=diag.diagnosis,
        location=_location(habl, file_path, line=_span(diag)),
        payload=payload,
    )


def _convert_advisory(habl: ModuleType, checker_name: str, diag: Any, file_path: str) -> Finding:
    """Advisory: primitive, message, location (SourceSpan), suggestion."""
    payload = _base_payload(checker_name, diag)
    payload["suggestion"] = diag.suggestion
    return habl.Finding(
        source_tool=SOURCE_TOOL,
        severity=habl.Severity.ADVISORY,
        category=habl.Category.STRUCTURAL,
        summary=diag.message,
        location=_location(habl, file_path, line=_span(diag)),
        payload=payload,
    )


def _convert_zero_return(habl: ModuleType, checker_name: str, diag: Any, file_path: str) -> Finding:
    """ZeroReturnDiagnostic (R3): function_name, lineno, col_offset,
    declared_return. The function name is a structured field here."""
    payload = _base_payload(checker_name, diag)
    payload["primitive"] = "R3"
    payload["declared_return"] = diag.declared_return
    payload["col_offset"] = diag.col_offset if isinstance(diag.col_offset, int) else None
    return habl.Finding(
        source_tool=SOURCE_TOOL,
        severity=habl.Severity.HIGH,
        category=habl.Category.STRUCTURAL,
        summary=diag.diagnosis,
        location=_location(habl, file_path, line=diag.lineno, function=diag.function_name),
        payload=payload,
    )


def _convert_onnx(habl: ModuleType, checker_name: str, diag: Any, file_path: str) -> Finding:
    """The five ONNX diagnostic dataclasses.

    Severity follows the CLI's own split: a ``severity`` field of
    "ADVISORY" is an advisory, anything else (the field is absent on
    four of the five types) is a MARAD. All structured fields except
    the diagnosis prose travel in the payload; ONNX has no source lines.
    """
    fields = {f.name: getattr(diag, f.name) for f in dataclasses.fields(diag)}
    summary = fields.pop("diagnosis")
    is_advisory = fields.get("severity") == "ADVISORY"
    payload = _base_payload(checker_name, diag)
    payload["fields"] = fields
    return habl.Finding(
        source_tool=SOURCE_TOOL,
        severity=habl.Severity.ADVISORY if is_advisory else habl.Severity.HIGH,
        category=habl.Category.STRUCTURAL,
        summary=summary,
        location=habl.Location(file=file_path),
        payload=payload,
    )


# ONNX types are matched by qualified name so this module never imports
# the ONNX adapter (and its optional dependencies) just to convert.
_ONNX_TYPES: frozenset[str] = frozenset(
    {
        "furqan_lint.onnx_adapter.runner.AllPathsEmitDiagnostic",
        "furqan_lint.onnx_adapter.runner.OpsetComplianceDiagnostic",
        "furqan_lint.onnx_adapter.numpy_divergence.NumpyDivergenceDiagnostic",
        "furqan_lint.onnx_adapter.score_validity.ScoreValidityDiagnostic",
        "furqan_lint.onnx_adapter.shape_coverage.ShapeCoverageDiagnostic",
    }
)


def _converter_for(diag: object) -> _Converter | None:
    if isinstance(diag, Marad):
        return _convert_marad
    if isinstance(diag, Advisory):
        return _convert_advisory
    if isinstance(diag, ZeroReturnDiagnostic):
        return _convert_zero_return
    cls = type(diag)
    if f"{cls.__module__}.{cls.__qualname__}" in _ONNX_TYPES:
        return _convert_onnx
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def diagnostics_to_findings(
    diagnostics: Sequence[tuple[str, object]],
    file_path: str,
) -> list[Finding]:
    """Convert (checker_name, diagnostic) pairs into HABL Findings.

    Raises :class:`HablUnavailableError` if HABL is unusable and
    :class:`UnsupportedDiagnosticError` on the first diagnostic with
    no converter. Construction validates every finding.
    """
    habl = require_habl()
    findings: list[Finding] = []
    for checker_name, diag in diagnostics:
        convert = _converter_for(diag)
        if convert is None:
            raise UnsupportedDiagnosticError(checker_name, type(diag).__qualname__)
        findings.append(convert(habl, checker_name, diag, file_path))
    return findings


def emit_to_ledger(
    diagnostics: Sequence[tuple[str, object]],
    file_path: str | Path,
    project_root: str | Path,
) -> int:
    """Convert diagnostics and append them to the HABL ledger in one batch.

    Returns the number of findings appended. Raises
    :class:`HablExportError` (or a subclass) if export did not complete;
    in that case nothing from this batch was written, unless the process
    or OS failed mid-write (HABL v0.1.1 is not crash-atomic).
    """
    habl = require_habl()
    recorded = ledger_file_path(file_path, project_root)
    try:
        findings = diagnostics_to_findings(diagnostics, recorded)
    except HablExportError:
        raise
    except (TypeError, ValueError) as exc:
        msg = f"could not build HABL findings for {recorded}: {exc}"
        raise HablExportError(msg) from exc
    if not findings:
        return 0
    try:
        habl.append_findings(findings, project_root)
    except (OSError, TypeError, ValueError) as exc:
        msg = f"HABL append failed for {recorded}: {exc}"
        raise HablExportError(msg) from exc
    return len(findings)


# Bismillah ir-Rahman ir-Rahim
