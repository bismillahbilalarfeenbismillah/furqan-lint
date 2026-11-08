# Bismillah ir-Rahman ir-Rahim

# furqan-lint

**Structural honesty checks for Python, Rust, Go, and ONNX.**

[![CI](https://github.com/BayyinahEnterprise/furqan-lint/actions/workflows/ci.yml/badge.svg)](https://github.com/BayyinahEnterprise/furqan-lint/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/badge/PyPI-furqan--lint-blue.svg)](https://pypi.org/project/furqan-lint/)

Your type signatures promise callers what they will get. furqan-lint checks whether the code keeps those promises. If a function says `-> str` but returns `None` on some path, if a caller silently drops an `Optional`, if your public API shrinks between releases -- furqan-lint fires. Structural honesty means the outward declaration and the inner behavior are the same thing.

> **Part of the [Entourage Effect Software Safety Suite (EESS)](https://github.com/bismillahbilalarfeenbismillah/eess-prototype)** -- six instruments that work together for evidence-based software safety. furqan-lint is the build-time visibility layer.

## The EESS Instruments

| Instrument | Role | Repository |
|-----------|------|------------|
| **Furqan** | The programming language | [furqan-programming-language](https://github.com/bismillahbilalarfeenbismillah/furqan-programming-language) |
| **furqan-lint** | Structural honesty linter (this repo) | [furqan-lint](https://github.com/bismillahbilalarfeenbismillah/furqan-lint) |
| **FATIMA** | Append-only file protection encoder | [FATIMA](https://github.com/bismillahbilalarfeenbismillah/FATIMA) |
| **HABL** | Finding protocol and evidence ledger | [habl](https://github.com/bismillahbilalarfeenbismillah/habl) |
| **Bayyinah** | File integrity scanner | [bayyinah-integrity-scanner](https://github.com/bismillahbilalarfeenbismillah/bayyinah-integrity-scanner) |
| **Shahid** | Weight-holder transparency reports | (in FATIMA repo, PR #7) |

## What It Catches

furqan-lint translates source into the [Furqan](https://tryfurqan.com) AST and runs checkers whose semantics carry across language boundaries.

### Core Python checks

- **D24 (all-paths-return)** -- every control-flow path through a typed function reaches a return statement.
- **D11 (status-coverage)** -- when a function returns `Optional[X]`, every caller either propagates the optionality or explicitly handles `None`. Silently collapsing `Optional[X]` into a non-optional return is flagged.
- **return_none_mismatch** -- a function declaring `-> str` that returns `None` on some path is flagged as a type mismatch.
- **additive_only** -- `furqan-lint diff old.py new.py` compares public surfaces and fires on any removed name. Additions are silent. Your API can grow; it cannot shrink.

### Multi-language adapters

| Adapter | Extra | Checks |
|---------|-------|--------|
| **Rust** | `[rust]` | R3 + D24 + D11 via tree-sitter |
| **Go** | `[go]` | D24 + D11 with `(T, error)` shape |
| **ONNX** | `[onnx]` | D24-onnx + opset-compliance + D11-onnx shape/type |
| **ONNX Runtime** | `[onnx-runtime]` | numpy-vs-ONNX divergence detection |
| **ONNX Profile** | `[onnx-profile]` | score-validity ADVISORY |
| **Gate 11** | `[gate11]` | Sigstore-CASM cryptographic API attestation |
| **Gate 11 Rust** | `[gate11-rust]` | Sigstore-CASM for Rust public surfaces |

## Install

```bash
pip install furqan-lint
```

Requires Python 3.10+ and `furqan>=0.11.0` (resolved automatically from PyPI).

```bash
# With adapters
pip install "furqan-lint[rust]"
pip install "furqan-lint[go]"
pip install "furqan-lint[onnx-runtime]"
pip install "furqan-lint[gate11]"
pip install "furqan-lint[rust,go,onnx-runtime,onnx-profile,gate11]"  # all
```

## Usage

```bash
furqan-lint check path/to/file.py        # check one file
furqan-lint check path/to/directory/      # check all files
furqan-lint diff old_version.py new.py    # additive-only API check
furqan-lint manifest init module.py       # Gate 11: sign public surface
furqan-lint manifest verify module.py     # Gate 11: verify signature
furqan-lint manifest verify-self          # verify furqan-lint's own attestation
```

## Example

```python
# example.py
from typing import Optional

def find_record(id: int) -> Optional[dict]:
    if id <= 0:
        return None
    return {"id": id}

def get_name(id: int) -> str:
    record = find_record(id)
    if record is not None:
        return record["name"]
    # Missing else: falls through with no return
```

```
$ furqan-lint check example.py

MARAD  example.py
  3 violation(s):
    [all_paths_return] Function 'get_name' at line 8 declares
    -> str but not every control-flow path reaches a return statement.

    [status_coverage] Function 'get_name' at line 8 calls
    'find_record' (returns Optional[dict]) but declares -> str.
    The Optional is silently collapsed.

    [return_none_mismatch] Function 'get_name' at line 8 declares
    -> str but returns None on at least one path.
```

## Sigstore-CASM Gate 11 -- Cryptographic API Stability

Gate 11 signs your module's public surface with Sigstore and records it in the Rekor transparency log. If a name is removed or a signature changes between releases, verification fails. The tool that catches drift in others' code attests its own -- from v1.0, furqan-lint signs its own releases with Gate 11.

```bash
pip install "furqan-lint[gate11]"
furqan-lint manifest init src/mylib.py            # OIDC sign
furqan-lint manifest verify src/mylib.py \
  --expected-identity "<pattern>" \
  --expected-issuer "https://token.actions.githubusercontent.com"
furqan-lint manifest update src/mylib.py          # additive-only re-sign
```

The complete CASM-V verification namespace (CASM-V-001 through CASM-V-072) covers bundle parsing, OIDC identity enforcement, module integrity, chain pointer verification, and self-attestation. See `SAFETY_INVARIANTS.md` for the seven canonical invariants.

## CI Integration

### GitHub Action

```yaml
name: Furqan Lint
on: [push, pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: BayyinahEnterprise/furqan-lint@v1.0.2
        with:
          path: src/
```

### Pre-Commit Hook

```yaml
repos:
  - repo: https://github.com/BayyinahEnterprise/furqan-lint
    rev: v1.0.2
    hooks:
      - id: furqan-lint
```

### Alongside ruff and mypy

| Tool | Catches | Overlap |
|------|---------|---------|
| **ruff** | Style, unused imports, complexity, formatting | None |
| **mypy** | Type errors, some missing returns | Partial on D24, return-None. mypy does NOT catch Optional collapse (D11) or API breaks (additive-only) |
| **furqan-lint** | Missing returns, Optional collapse, return-None mismatch, API stability | See mypy column |

```yaml
# .pre-commit-config.yaml -- recommended stack
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.13.0
    hooks:
      - id: mypy
  - repo: https://github.com/BayyinahEnterprise/furqan-lint
    rev: v1.0.2
    hooks:
      - id: furqan-lint
```

## Structural Honesty and the Bayyinah Structural Safety Gate

furqan-lint implements the build-time layer of the Structural Honesty Verification (SHV) framework. The Bayyinah Structural Safety Gate (BSSG) is the evidence-independent control plane that determines whether a claim about code can be trusted -- furqan-lint provides the evidence that feeds those decisions.

The core thesis: a function's type signature is a claim. A caller's use of a return value is a trust decision. If the claim and the behavior diverge, the system has performed alignment -- it displays one thing while containing another. furqan-lint makes the divergence visible; the engineer performs the recognition.

## Documented Limitations

Every limitation has a fixture in `tests/fixtures/documented_limits/` and a pinning test in `tests/test_documented_limits.py`. The full list covers Python, Rust, Go, and ONNX adapter boundaries. See the [CHANGELOG](CHANGELOG.md) for per-version closure ledgers.

Key boundaries:
- Checks are syntactic heuristics over each function's AST, not semantic proofs
- Only four of Furqan's ten checkers run on Python (the rest need language-specific conventions)
- `return_none_mismatch` catches the `None` literal only, not type-incorrect returns
- Exhaustive `match` recognition is conservative (D24 cannot prove a `case _:` arm covers all paths)
- D11 narrowing stops at function boundaries and adjacent guards only

## Contributing

```bash
git clone https://github.com/BayyinahEnterprise/furqan-lint.git
cd furqan-lint
pip install -e ".[dev]"
pre-commit install
pytest -q
```

## License

Apache-2.0. See [LICENSE](LICENSE).

---

*Author: Bilal Syed Arfeen. furqan-lint is the structural-honesty instrument of the Entourage Effect Software Safety Suite.*

# Bismillah ir-Rahman ir-Rahim
