# Bismillah ir-Rahman ir-Rahim
"""Tests for the furqan-lint HABL emitter (GPT-6 design review of 3f6e80b).

Covers: one declared converter per diagnostic type, no prose-derived
locations, unsupported diagnostics refused before any write, fail-closed
export when --habl is requested (exit 3), and a round trip through
HABL's own reader.
"""
# Bismillah ir-Rahman ir-Rahim

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

habl = pytest.importorskip("habl")

from furqan.errors.marad import Advisory, Marad  # noqa: E402
from furqan.parser.ast_nodes import SourceSpan  # noqa: E402
from habl import Severity, ledger_path, read_ledger  # noqa: E402
from habl.query import load_ledger  # noqa: E402

from furqan_lint import cli, habl_emitter  # noqa: E402
from furqan_lint.habl_emitter import (  # noqa: E402
    HablExportError,
    HablUnavailableError,
    UnsupportedDiagnosticError,
    diagnostics_to_findings,
    emit_to_ledger,
    ledger_file_path,
    require_habl,
)
from furqan_lint.zero_return import ZeroReturnDiagnostic  # noqa: E402

FAILING = Path(__file__).parent / "fixtures" / "failing"
CLEAN = Path(__file__).parent / "fixtures" / "clean"


class _Unknown:
    """A diagnostic type with no declared converter."""

    def __str__(self) -> str:
        return "something new"


def _marad(line: int = 4) -> Marad:
    return Marad(
        primitive="D24",
        diagnosis="function 'process' falls through on one path",
        location=SourceSpan(file="x.py", line=line, column=0),
        minimal_fix="add a return",
        regression_check="rerun",
    )


def _advisory() -> Advisory:
    return Advisory(
        primitive="D11",
        message="note about 'helper'",
        location=SourceSpan(file="x.py", line=9, column=2),
        suggestion="consider X",
    )


def _zero_return() -> ZeroReturnDiagnostic:
    return ZeroReturnDiagnostic(function_name="f", lineno=3, col_offset=0, declared_return="str")


def _run_cli(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "argv", ["furqan-lint", *argv])
    return cli.main()


# --- converters -----------------------------------------------------------


def test_core_types_convert_with_structured_fields_only():
    findings = diagnostics_to_findings(
        [
            ("all_paths_return", _marad()),
            ("status_coverage", _advisory()),
            ("zero_return_path", _zero_return()),
        ],
        "pkg/x.py",
    )
    m, a, z = findings
    assert (m.severity, a.severity, z.severity) == (Severity.HIGH, Severity.ADVISORY, Severity.HIGH)
    assert (m.location.line, a.location.line, z.location.line) == (4, 9, 3)
    # The Marad's prose names 'process'; it must NOT become a location.
    assert m.location.function is None
    assert a.location.function is None
    # ZeroReturnDiagnostic carries the name as a structured field.
    assert z.location.function == "f"
    assert m.payload["primitive"] == "D24" and m.payload["minimal_fix"] == "add a return"
    assert a.payload["suggestion"] == "consider X"
    assert z.payload == {
        "primitive": "R3",
        "checker": "zero_return_path",
        "diagnostic_type": "ZeroReturnDiagnostic",
        "declared_return": "str",
        "col_offset": 0,
    }
    assert {f.location.file for f in findings} == {"pkg/x.py"}


def test_onnx_types_convert_and_keep_every_structured_field():
    from furqan_lint.onnx_adapter.numpy_divergence import NumpyDivergenceDiagnostic
    from furqan_lint.onnx_adapter.runner import AllPathsEmitDiagnostic, OpsetComplianceDiagnostic
    from furqan_lint.onnx_adapter.score_validity import ScoreValidityDiagnostic
    from furqan_lint.onnx_adapter.shape_coverage import ShapeCoverageDiagnostic

    diags = [
        ("all_paths_emit", AllPathsEmitDiagnostic("y", "output y unreachable", "wire y")),
        ("opset", OpsetComplianceDiagnostic("Foo", "n1", "", 13, "Foo not in opset 13", "use Bar")),
        (
            "shape",
            ShapeCoverageDiagnostic(
                "Concat", "ShapeInferenceError", "m", "d", "f", "shape_mismatch"
            ),
        ),
        (
            "numpy",
            NumpyDivergenceDiagnostic(
                "m.onnx", "r.py", "t.json", 2, "a", "b", "c", "diverges", "fix"
            ),
        ),
        (
            "score",
            ScoreValidityDiagnostic("Gemm", "KeyError", "k", "ADVISORY", "score invalid", "fix"),
        ),
    ]
    findings = diagnostics_to_findings(diags, "model.onnx")
    assert [f.severity for f in findings] == [Severity.HIGH] * 4 + [Severity.ADVISORY]
    assert findings[0].summary == "output y unreachable"
    assert findings[1].payload["fields"] == {
        "op_type": "Foo",
        "node_name": "n1",
        "domain": "",
        "opset_version": 13,
        "minimal_fix": "use Bar",
    }
    assert findings[3].payload["fields"]["probe_index"] == 2
    assert all("diagnosis" not in f.payload["fields"] for f in findings)
    assert all(f.location.line is None for f in findings)


# --- unsupported diagnostics ----------------------------------------------


def test_unknown_type_is_refused_not_downgraded(tmp_path):
    with pytest.raises(UnsupportedDiagnosticError) as exc:
        emit_to_ledger([("brand_new", _Unknown())], tmp_path / "x.py", tmp_path)
    assert exc.value.checker_name == "brand_new"
    assert exc.value.diagnostic_type == "_Unknown"
    assert isinstance(exc.value, HablExportError)
    assert not ledger_path(tmp_path).exists()


def test_unsupported_in_batch_writes_nothing_from_the_batch(tmp_path):
    with pytest.raises(UnsupportedDiagnosticError):
        emit_to_ledger(
            [("all_paths_return", _marad()), ("brand_new", _Unknown())],
            tmp_path / "x.py",
            tmp_path,
        )
    assert not ledger_path(tmp_path).exists()


# --- HABL availability and compatibility ----------------------------------


def test_missing_habl_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "habl", None)
    with pytest.raises(HablUnavailableError, match="not installed"):
        require_habl()


@pytest.mark.parametrize("version", ["0.1.0", "0.2.0", "1.0.0", "", None, "dev"])
def test_incompatible_habl_version_raises(monkeypatch, version):
    monkeypatch.setattr(habl, "__version__", version)
    with pytest.raises(HablUnavailableError, match="not supported"):
        require_habl()


def test_missing_habl_api_raises(monkeypatch):
    monkeypatch.delattr(habl, "append_findings")
    with pytest.raises(HablUnavailableError, match="append_findings"):
        require_habl()


def test_append_failure_is_an_export_error(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(habl, "append_findings", boom)
    with pytest.raises(HablExportError, match="disk full"):
        emit_to_ledger([("all_paths_return", _marad())], tmp_path / "x.py", tmp_path)


# --- paths ------------------------------------------------------------------


def test_ledger_path_is_root_relative_posix(tmp_path):
    (tmp_path / "pkg").mkdir()
    assert ledger_file_path(tmp_path / "pkg" / "x.py", tmp_path) == "pkg/x.py"
    outside = tmp_path.parent / "elsewhere.py"
    assert ledger_file_path(outside, tmp_path / "pkg") == str(outside)


# --- CLI ----------------------------------------------------------------------


def test_cli_habl_writes_findings_that_habl_reads_back(tmp_path, monkeypatch, capsys):
    for name in ("missing_return_path.py", "zero_return_function.py", "status_collapse.py"):
        shutil.copy(FAILING / name, tmp_path / name)
    code = _run_cli(["check", "--habl", str(tmp_path)], monkeypatch)
    assert code == 1  # MARAD verdict, export complete
    assert "HABL EXPORT FAILED" not in capsys.readouterr().err
    load = load_ledger(tmp_path)
    assert load.malformed == []
    files = sorted(f.location.file for f in load.findings)
    assert files == ["missing_return_path.py", "status_collapse.py", "zero_return_function.py"]
    assert {f.source_tool for f in load.findings} == {"furqan-lint"}
    assert len(read_ledger(tmp_path)) == 3


def test_cli_clean_file_writes_nothing(tmp_path, monkeypatch):
    shutil.copy(CLEAN / "simple_function.py", tmp_path / "ok.py")
    assert _run_cli(["check", "--habl", str(tmp_path / "ok.py")], monkeypatch) == 0
    assert not ledger_path(tmp_path).exists()


def test_cli_habl_missing_exits_3_before_scanning(tmp_path, monkeypatch, capsys):
    shutil.copy(FAILING / "missing_return_path.py", tmp_path / "bad.py")
    monkeypatch.setitem(sys.modules, "habl", None)
    code = _run_cli(["check", "--habl", str(tmp_path)], monkeypatch)
    out = capsys.readouterr()
    assert code == cli.EXIT_HABL_EXPORT_FAILED == 3
    assert "not installed" in out.err
    assert "MARAD" not in out.out  # failed before scanning


def test_cli_unsupported_diagnostic_exits_3_and_writes_nothing(tmp_path, monkeypatch, capsys):
    shutil.copy(FAILING / "missing_return_path.py", tmp_path / "bad.py")
    monkeypatch.setattr(habl_emitter, "_converter_for", lambda _d: None)
    code = _run_cli(["check", "--habl", str(tmp_path)], monkeypatch)
    out = capsys.readouterr()
    assert code == 3
    assert "HABL EXPORT FAILED" in out.err and "no HABL converter" in out.err
    assert "MARAD" in out.out  # lint report itself still complete
    assert not ledger_path(tmp_path).exists()


def test_cli_marad_and_export_failure_are_reported_separately(tmp_path, monkeypatch, capsys):
    """GPT-6 review: exit 3 may take precedence over MARAD only if the
    MARAD findings stay visible and the two failures are distinguishable."""
    shutil.copy(FAILING / "missing_return_path.py", tmp_path / "bad.py")
    shutil.copy(CLEAN / "simple_function.py", tmp_path / "ok.py")

    def boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(habl, "append_findings", boom)
    code = _run_cli(["check", "--habl", str(tmp_path)], monkeypatch)
    out = capsys.readouterr()
    assert code == 3
    # The structural finding is on stdout, exactly as without --habl.
    assert "MARAD" in out.out and "all_paths_return" in out.out
    assert "HABL" not in out.out.replace("HABL:", "")
    # stderr names both facts on separate lines.
    err_lines = out.err.splitlines()
    assert any(line.startswith("Lint verdict: MARAD (exit 1)") for line in err_lines)
    assert any(line.startswith("HABL EXPORT FAILED") and "read-only" in line for line in err_lines)
    assert any(line.startswith("HABL export incomplete") for line in err_lines)


def test_cli_append_failure_exits_3(tmp_path, monkeypatch, capsys):
    shutil.copy(FAILING / "missing_return_path.py", tmp_path / "bad.py")

    def boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(habl, "append_findings", boom)
    assert _run_cli(["check", "--habl", str(tmp_path)], monkeypatch) == 3
    assert "read-only file system" in capsys.readouterr().err


def test_cli_without_habl_flag_is_unchanged_and_state_does_not_leak(tmp_path, monkeypatch):
    shutil.copy(FAILING / "missing_return_path.py", tmp_path / "bad.py")
    assert _run_cli(["check", "--habl", str(tmp_path)], monkeypatch) == 1
    before = ledger_path(tmp_path).read_bytes()
    # A second in-process run without --habl must not write.
    assert _run_cli(["check", str(tmp_path)], monkeypatch) == 1
    assert ledger_path(tmp_path).read_bytes() == before


# Bismillah ir-Rahman ir-Rahim
