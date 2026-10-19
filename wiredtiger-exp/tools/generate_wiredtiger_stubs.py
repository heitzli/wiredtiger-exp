#!/usr/bin/env python3
"""Generate complete PEP 561 type stubs for the installed `wiredtiger` package.

The runtime module is the single source of truth: `wiredtiger/__init__.py`
copies every public symbol out of the SWIG-generated `swig_wiredtiger` module,
so stubs are produced by *importing* the installed package (mypy's stubgen in
inspect mode) rather than by parsing anything. This closes the gap between
SWIG's view of the API (C proxies: `_set_key`, C-style signatures, `int`
returns) and the public Python API the test suite and examples actually use.

Pipeline:
  1. stubgen --inspect-mode on `wiredtiger.swig_wiredtiger`
     -> the full public API (pythoncode-defined wrappers such as
        Cursor.set_key / __iter__ / stat.* are visible at runtime).
  2. stubgen --inspect-mode on `wiredtiger._wiredtiger`
     -> the C extension stub, the import target of (1)'s output.
  3. Normalize two known generator artifacts:
     a) stubgen records names imported from the C extension as coming from a
        top-level module "_wiredtiger"; in the flat installed layout they live
        inside the package, so rewrite the import to relative "._wiredtiger".
     b) SWIG's C docstrings misreport Python-visible returns: the C function
        wiredtiger_open is documented "-> int" but returns a Connection
        (it builds the object from the WT_CONNECTION** out-parameter).
        Correct the declared return type at the source.
  4. Emit `wiredtiger/__init__.pyi`: a star re-export that mirrors what
     `wiredtiger/__init__.py` does at runtime, plus its own restart_python().
  5. Create the empty PEP 561 marker `py.typed`. Without it mypy silently
     ignores inline stubs of installed packages.

Writes into the fork:  <fork>/lang/python/wiredtiger/
  - __init__.pyi          (emitted here)
  - swig_wiredtiger.pyi   (stubgen + normalization)
  - _wiredtiger.pyi       (stubgen + normalization)
  - py.typed              (empty marker)

`lang/python/setup_pip.py` stages those files into the sdist automatically.

Usage:
    uv run tools/generate_wiredtiger_stubs.py [--fork ../wiredtiger] [--check]

Requires the package to be importable in the current interpreter environment
(run via `uv run`, after `uv sync` has installed wiredtiger).
"""

from __future__ import annotations

import argparse
import filecmp
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Function-pointer members of the WT structs are wrapped as builtin methods
# without introspectable signatures (and without raising on error, the call
# returns the WT error code, 0 == OK; failures raise WiredTigerError). stubgen
# therefore emits them as `name: ClassVar[builtin_function_or_method]`, which
# type checkers cannot call or document meaningfully. The mechanical pass
# converts them to methods; this table then refines the public API surface
# with exact signatures (validated against the runtime), one def per member.
# (class name or "" for module level, member, full def line) -- rendered into
# a flat table; the convention below keys on (class, member).
_t = "str | None"
_SIGNATURE_LINES: dict[tuple[str, str], str] = {
    ("", "wiredtiger_open"):
        'def wiredtiger_open(home: str | None = ..., config: str | None = ..., /) -> "Connection": ...',
    ("", "wiredtiger_repair"):
        'def wiredtiger_repair(connection: "Connection", config: str | None = ..., /) -> int: ...',
    ("", "wiredtiger_strerror"):
        'def wiredtiger_strerror(error: int, /) -> str | None: ...',
    ("Connection", "open_session"):
        f'def open_session(self, config: {_t} = ..., /) -> "Session": ...',
    ("Connection", "close"):
        f'def close(self, config: {_t} = ..., /) -> int: ...',
    ("Connection", "get_home"):
        'def get_home(self) -> str | None: ...',
    ("Connection", "is_new"):
        'def is_new(self) -> int: ...',
    ("Connection", "reconfigure"):
        f'def reconfigure(self, config: {_t} = ..., /) -> int: ...',
    ("Session", "open_cursor"):
        'def open_cursor(self, uri: str | None = ..., to_dup: "Cursor | None" = ..., config: str | None = ..., /) -> "Cursor": ...',
    ("Session", "close"):
        f'def close(self, config: {_t} = ..., /) -> int: ...',
}
for _key in (("Session", "create"), ("Session", "drop"),
             ("Session", "alter"), ("Session", "compact"),
             ("Session", "salvage"), ("Session", "verify"),
             ("Session", "truncate")):
    _SIGNATURE_LINES[_key] = \
        f'def {_key[1]}(self, uri: str, config: {_t} = ..., /) -> int: ...'
for _key in (("Session", "reconfigure"), ("Session", "checkpoint"),
             ("Session", "log_flush"), ("Session", "begin_transaction"),
             ("Session", "commit_transaction"),
             ("Session", "prepare_transaction"),
             ("Session", "rollback_transaction")):
    _SIGNATURE_LINES[_key] = \
        f'def {_key[1]}(self, config: {_t} = ..., /) -> int: ...'
for _key in (("Cursor", "next"), ("Cursor", "prev"), ("Cursor", "reset"),
             ("Cursor", "search"), ("Cursor", "insert"), ("Cursor", "update"),
             ("Cursor", "remove"), ("Cursor", "reserve"),
             ("Cursor", "checkpoint_id"), ("Cursor", "largest_key"),
             ("Cursor", "search_near"), ("Cursor", "compare"),
             ("Cursor", "equals")):
    _SIGNATURE_LINES[_key] = f'def {_key[1]}(self, *args: Any) -> int: ...'
_SIGNATURE_LINES[("Cursor", "set_key")] = \
    'def set_key(self, key: Any, /) -> None: ...'
_SIGNATURE_LINES[("Cursor", "set_value")] = \
    'def set_value(self, value: Any, /) -> None: ...'
for _key in (("Cursor", "get_key"), ("Cursor", "get_value"),
             ("Cursor", "get_keys"), ("Cursor", "get_values"),
             ("Cursor", "get_raw_key_value")):
    _SIGNATURE_LINES[_key] = f'def {_key[1]}(self) -> Any: ...'
_SIGNATURE_LINES[("Cursor", "modify")] = \
    'def modify(self, modifies: list[Any], /) -> int: ...'
# --- probe-validated remainder of the public API (return types and argument
# counts confirmed against the built extension; failures raise
# WiredTigerError, successes return the WT error code 0 unless noted).
_SIGNATURE_LINES[("Cursor", "bound")] = \
    'def bound(self, action: str | None = ..., /) -> int: ...'
_SIGNATURE_LINES[("Cursor", "reconfigure")] = \
    f'def reconfigure(self, config: {_t} = ..., /) -> int: ...'
_SIGNATURE_LINES[("Session", "reset")] = 'def reset(self) -> int: ...'
_SIGNATURE_LINES[("Session", "reset_snapshot")] = \
    'def reset_snapshot(self) -> int: ...'
_SIGNATURE_LINES[("Session", "breakpoint")] = \
    'def breakpoint(self) -> int: ...'
_SIGNATURE_LINES[("Session", "publish")] = \
    'def publish(self, message: str, /) -> int: ...'
_SIGNATURE_LINES[("Session", "strerror")] = \
    'def strerror(self, error: int, /) -> str | None: ...'
_SIGNATURE_LINES[("Session", "log_printf")] = \
    'def log_printf(self, *args: Any) -> int: ...'
_SIGNATURE_LINES[("Session", "get_last_error")] = \
    'def get_last_error(self) -> "list[int | str]": ...'
_SIGNATURE_LINES[("Session", "timestamp_transaction")] = \
    f'def timestamp_transaction(self, config: {_t} = ..., /) -> int: ...'
_SIGNATURE_LINES[("Session", "timestamp_transaction_uint")] = \
    'def timestamp_transaction_uint(self, which: int, ts: int, /) -> int: ...'
_SIGNATURE_LINES[("Session", "query_timestamp")] = \
    f'def query_timestamp(self, config: {_t} = ..., /) -> str: ...'
_SIGNATURE_LINES[("Session", "transaction_pinned_range")] = \
    'def transaction_pinned_range(self) -> "list[int]": ...'
_SIGNATURE_LINES[("Session", "prepared_id_transaction")] = \
    'def prepared_id_transaction(self, cursor: "Cursor | None" = ..., /) -> int: ...'
_SIGNATURE_LINES[("Session", "bind_configuration")] = \
    'def bind_configuration(self, uri: str, compiled: Any, /) -> int: ...'
_SIGNATURE_LINES[("Session", "truncate")] = (
    f'def truncate(self, uri: {_t} = ..., start: "Cursor | None" = ..., '
    f'stop: "Cursor | None" = ..., config: {_t} = ..., /) -> int: ...')
_SIGNATURE_LINES[("Connection", "rollback_to_stable")] = \
    f'def rollback_to_stable(self, config: {_t} = ..., /) -> int: ...'
_SIGNATURE_LINES[("Connection", "set_timestamp")] = \
    f'def set_timestamp(self, config: {_t} = ..., /) -> int: ...'
_SIGNATURE_LINES[("Connection", "set_context_uint")] = \
    'def set_context_uint(self, which: int, value: int, /) -> int: ...'
_SIGNATURE_LINES[("Connection", "get_page_log")] = \
    'def get_page_log(self, id: int, config: str, /) -> Any: ...'
_SIGNATURE_LINES[("Connection", "debug_info")] = \
    f'def debug_info(self, config: {_t} = ..., /) -> int: ...'
_SIGNATURE_LINES[("Connection", "load_extension")] = \
    f'def load_extension(self, path: str, config: {_t} = ..., /) -> int: ...'
_SIGNATURE_LINES[("Connection", "compile_configuration")] = \
    'def compile_configuration(self, config: str, type: int, prefix: str, /) -> Any: ...'
_SIGNATURE_LINES[("Connection", "configure_method")] = (
    'def configure_method(self, method_name: str, uri: str, config: str, '
    'ok_to_process: int, session_method_check: str, /) -> int: ...')
_SIGNATURE_LINES[("Connection", "set_file_system")] = \
    'def set_file_system(self, file_system: "FileSystem | None", /) -> int: ...'
_SIGNATURE_LINES[("Connection", "set_key_provider")] = \
    'def set_key_provider(self, provider: "KeyProvider | None", /) -> int: ...'
_SIGNATURE_LINES[("Connection", "query_timestamp")] = \
    f'def query_timestamp(self, config: {_t} = ..., /) -> str: ...'
_SIGNATURE_LINES[("Connection", "dump_error_log")] = \
    'def dump_error_log(self) -> int: ...'
_SIGNATURE_LINES[("", "diagnostic_build")] = \
    'def diagnostic_build() -> int: ...'
_SIGNATURE_LINES[("", "standalone_build")] = \
    'def standalone_build() -> int: ...'
_SIGNATURE_LINES[("", "wiredtiger_version")] = \
    'def wiredtiger_version() -> "list[str | int]": ...'
_SIGNATURE_LINES[("", "wiredtiger_get_verbose_categories")] = \
    'def wiredtiger_get_verbose_categories() -> "list[tuple[str, str]]": ...'
_SIGNATURE_LINES[("", "wiredtiger_calc_modify")] = (
    'def wiredtiger_calc_modify(session: "Session", oldv: Any, newv: Any, '
    'maxdiff: int, nmod: "list[Any]") -> int: ...')
_SIGNATURE_LINES[("", "wiredtiger_calc_modify_string")] = (
    'def wiredtiger_calc_modify_string(session: "Session", oldv: str, newv: str, '
    'maxdiff: int, nmod: "list[Any]") -> int: ...')
_SIGNATURE_LINES[("", "wiredtiger_dump_error_log")] = \
    'def wiredtiger_dump_error_log(callback: "Callable[..., Any] | None" = ...) -> int: ...'

INIT_PYI_TEMPLATE = """\
# Auto-generated by tools/generate_wiredtiger_stubs.py -- do not edit.
# Mirrors wiredtiger/__init__.py, which copies the public swig_wiredtiger
# namespace into the wiredtiger package at import time.
from .swig_wiredtiger import *


def restart_python() -> None: ...
"""

# Copydoc resolution ----------------------------------------------------------
# SWIG translates C doxygen comments into runtime __doc__ strings, but copies
# "@copydoc <target>" directives over verbatim instead of resolving them. The
# index built below maps (WT_STRUCT or "", member) to the doxygen comment block
# preceding the declaration in wiredtiger.h(.in), so stub docstrings can show
# the actual C documentation instead of the bare directive.

_PYCLASS_TO_STRUCT = {
    "Connection": "WT_CONNECTION",
    "Session": "WT_SESSION",
    "Cursor": "WT_CURSOR",
    "FileHandle": "WT_FILE_HANDLE",
    "FileSystem": "WT_FILE_SYSTEM",
    "KeyProvider": "WT_KEY_PROVIDER",
    "CryptKeys": "WT_CRYPT_KEYS",
    "PageLog": "WT_PAGE_LOG",
    "PageLogHandle": "WT_PAGE_LOG_HANDLE",
}

_STRUCT_OPEN_RE = re.compile(r"\bstruct\s+__(\w+)\s*\{")
_MEMBER_RE = re.compile(r"\(\*\s*(\w+)\s*\)\s*\(")
_MACRO_MEMBER_RE = re.compile(r"__F\(\s*(\w+)\s*\)\s*\(")
_COPYDOC_RE = re.compile(r"@copydoc\s+([A-Za-z_][\w:]*)")
_FAKE_SIG_RE = re.compile(r"^\w+\s*\(.*\)\s*(?:->.*)?$")
_CLASS_RE = re.compile(r"^class (?P<name>\w+)")


def _decl_ident(line: str) -> str | None:
    # The identifier immediately preceding the first "(" in a declaration.
    p = line.find("(")
    if p <= 0:
        return None
    j = p
    while j > 0 and (line[j - 1].isalnum() or line[j - 1] == "_"):
        j -= 1
    name = line[j:p]
    return name if name.isidentifier() else None


# build_header_doc_index --
#   Single pass over the public C header: every doxygen comment block is
#   attached to the declaration that follows it -- struct function-pointer
#   members are keyed (WT_STRUCT, member), free functions ("", name).
def build_header_doc_index(header: Path) -> dict[tuple[str, str], list[str]]:
    docs: dict[tuple[str, str], list[str]] = {}
    current_struct = ""
    in_comment = False
    pending: list[str] = []
    for raw in header.read_text().splitlines():
        stripped = raw.strip()
        if in_comment:
            if "*/" in stripped:
                pre = stripped.split("*/", 1)[0]
                if pre.startswith("*"):
                    pre = pre[1:]
                if pre.strip():
                    pending.append(pre.strip())
                in_comment = False
                continue
            body_line = stripped
            if body_line.startswith("*"):
                body_line = body_line[1:]
            pending.append(body_line.strip())
            continue
        if stripped.startswith("/*"):
            in_comment = True
            pending = []
            inner = stripped[2:].lstrip("!")   # /*! -> doxygen opener
            if inner.endswith("*/"):
                inner = inner[:-2]
                in_comment = False
            if inner.strip():
                pending.append(inner.strip())
            continue
        if not stripped:
            continue
        if pending:
            while pending and not pending[0]:
                pending.pop(0)
            while pending and not pending[-1]:
                pending.pop()
            if pending:
                member = (_MEMBER_RE.search(stripped)
                          or _MACRO_MEMBER_RE.search(stripped))
                if member:
                    docs[(current_struct, member.group(1))] = list(pending)
                else:
                    name = _decl_ident(stripped)
                    if name:
                        docs[("", name)] = list(pending)
            pending = []
        if stripped.startswith("}"):
            current_struct = ""
            continue
        struct_open = _STRUCT_OPEN_RE.search(stripped)
        if struct_open:
            # struct __wt_cursor { -> WT_CURSOR
            current_struct = struct_open.group(1).upper()
    return docs


# fix_docstring_bodies --
#   stubgen places a docstring under the original `member: ClassVar[...]`
#   declaration; once that line is converted to `def member(self): ...`, the
#   docstring must become the def's body, not trail the one-line `...`.
def fix_docstring_bodies(lines: list[str]) -> list[str]:
    out: list[str] = []
    for idx, line in enumerate(lines):
        stripped = line.rstrip()
        if stripped.endswith(": ..."):
            nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
            if nxt.lstrip().startswith('"""'):
                stripped = stripped[:-4]
        out.append(stripped)
    return out


# _copydoc_block --
#   Resolve one @copydoc target to its doxygen text, or None.
def _copydoc_block(target: str, current_class: str,
                   docs: dict[tuple[str, str], list[str]]) -> list[str] | None:
    if target.startswith("class::"):
        name = target.split("::", 1)[1]
        struct = _PYCLASS_TO_STRUCT.get(current_class, "")
    elif "::" in target:
        struct, name = target.split("::", 1)
        struct = struct.lstrip("_").upper()
    else:
        return docs.get(("", target))
    # The python PageLog binding drops the pl_ prefix of the C members.
    for candidate in (name, "pl_" + name):
        if (block := docs.get((struct, candidate))) is not None:
            return block
    return None


def _emit_docstring(inner: list[str], indent: str) -> list[str]:
    # Escape backslashes so doxygen markup (\c, \1, ...) survives re-parsing
    # of the stub without SyntaxWarning about invalid escape sequences.
    lines = [ln.replace("\\", "\\\\") for ln in inner]
    if not lines:
        return [indent + '""""""']
    if len(lines) == 1:
        return [indent + '"""' + lines[0] + '"""']
    out = [indent + '"""' + lines[0]]
    for ln in lines[1:-1]:
        out.append(indent + ln if ln else "")
    if lines[-1]:
        out.append(indent + lines[-1] + '"""')
    else:
        out.append('"""')
    return out


# resolve_copydocs --
#   Replace "@copydoc <target>" directives inside stub docstrings with the
#   resolved C documentation (and drop SWIG's fake C-signature first line).
#   A docstring is left untouched if any of its targets fail to resolve.
def resolve_copydocs(lines: list[str],
                     docs: dict[tuple[str, str], list[str]]) -> list[str]:
    out: list[str] = []
    current_class = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        class_match = _CLASS_RE.match(line)
        if class_match:
            current_class = class_match.group("name")
            out.append(line)
            i += 1
            continue
        stripped = line.strip()
        if not stripped.startswith('"""'):
            out.append(line)
            i += 1
            continue
        indent = line[:len(line) - len(line.lstrip())]
        inner: list[str] = []
        first = stripped[3:]
        closed = False
        if '"""' in first:
            inner.append(first[:first.index('"""')])
            closed = True
        else:
            inner.append(first)
        i += 1
        while not closed and i < len(lines):
            nxt = lines[i]
            if '"""' in nxt:
                inner.append(nxt[:nxt.index('"""')])
                closed = True
            else:
                inner.append(nxt.strip())
            i += 1
        targets = [m.group(1) for ln in inner for m in _COPYDOC_RE.finditer(ln)]
        blocks = [_copydoc_block(t, current_class, docs) for t in targets]
        if not targets or any(b is None for b in blocks):
            for target, block in zip(targets, blocks):
                if block is None:
                    print(
                        f"note: unresolved copydoc {target} "
                        f"(class {current_class or '<module>'})",
                        file=sys.stderr,
                    )
            out.extend(_emit_docstring(inner, indent))
            continue
        body = inner[1:] if _FAKE_SIG_RE.match(inner[0].strip()) else list(inner)
        new: list[str] = []
        for ln in body:
            if _COPYDOC_RE.search(ln):
                if new and new[-1]:
                    new.append("")
                new.extend(blocks.pop(0))
            elif ln.strip():
                if new and new[-1]:
                    new.append("")
                new.append(ln.strip())
        out.extend(_emit_docstring(new, indent))
    return out


def die(message: str) -> None:
    print(f"generate_wiredtiger_stubs: {message}", file=sys.stderr)
    sys.exit(1)


# find_fork --
#   Locate the wiredtiger fork: --fork if given, else the first neighboring
#   directory that looks like a wiredtiger checkout.
def find_fork(explicit: str | None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    cwd = Path.cwd()
    for rel in ("../wiredtiger", "../../wiredtiger"):
        candidates.append((cwd / rel).resolve())
    script_root = Path(__file__).resolve().parent.parent.parent
    candidates.append(script_root / "wiredtiger")
    for candidate in candidates:
        if (candidate / "lang" / "python" / "setup_pip.py").is_file():
            return candidate.resolve()
    die(
        "could not locate the wiredtiger fork; pass --fork /path/to/wiredtiger "
        "(expected a sibling directory containing lang/python/setup_pip.py)"
    )


# run_stubgen --
#   Run stubgen in inspect mode for one module, into dest_dir.
def run_stubgen(stubgen_bin: list[str], module: str, out_dir: Path) -> Path:
    result = subprocess.run(
        stubgen_bin + ["-m", module, "--inspect-mode",
                       "--include-docstrings", "-o", str(out_dir)],
        capture_output=True,
        text=True,
    )
    generated = out_dir / Path(*module.split(".")).with_suffix(".pyi")
    if result.returncode != 0 or not generated.is_file():
        die(
            f"stubgen failed for {module}:\n{result.stdout}\n{result.stderr}\n"
            "is wiredtiger installed in the current environment (uv sync)?"
        )
    return generated


# patch_relative_import --
#   Rewrite stubgen's absolute "from _wiredtiger import ..." to the relative
#   form the flat installed package actually resolves.
def patch_relative_import(path: Path) -> None:
    text = path.read_text()
    fixed = text.replace(
        "from _wiredtiger import", "from ._wiredtiger import"
    )
    if fixed != text:
        path.write_text(fixed)


_DECL_RE = re.compile(
    r"^(?P<indent>\s+)(?P<name>\w+): ClassVar\[builtin_function_or_method\] = \.\.\.$"
)
_DEF_RE = re.compile(r"^(?P<indent>\s+)def (?P<name>\w+)\(")
_CLASS_RE = re.compile(r"^class (?P<name>\w+)")


# patch_signatures --
#   Give every exposed function a usable signature:
#   1. function-pointer members (`name: ClassVar[builtin_function_or_method]`)
#      become `def name(self, ...) -> Any` so they are proper methods;
#   2. duplicates are dropped (pythoncode-defined wrappers win over the
#      mechanical conversion) so each member is declared exactly once -- this
#      removes the `Unknown | ((...))` unions type checkers report;
#   3. the SIGNATURES table refines the public API with exact signatures
#      validated against the runtime.
def patch_signatures(path: Path, is_ext_stub: bool) -> set[str]:
    lines = path.read_text().splitlines()

    # Collect member declarations while walking the file, tracking the
    # enclosing class ("": module level).
    members: dict[tuple[str, str], list[tuple[int, str]]] = {}
    current_class = ""
    for idx, line in enumerate(lines):
        class_match = _CLASS_RE.match(line)
        if class_match:
            current_class = class_match.group("name")
            continue
        decl = _DECL_RE.match(line)
        if decl:
            members.setdefault(
                (current_class, decl.group("name")), []
            ).append((idx, "decl"))
            continue
        definition = _DEF_RE.match(line)
        if definition and current_class:
            members.setdefault(
                (current_class, definition.group("name")), []
            ).append((idx, "def"))

    drop: set[int] = set()
    replace: dict[int, str] = {}

    # Pass 1: mechanical conversion of builtin ClassVar members; prefer an
    # existing pythoncode def when a member has both.
    for (cls, name), occurrences in members.items():
        if cls == "":
            continue
        decls = [i for i, kind in occurrences if kind == "decl"]
        defs = [i for i, kind in occurrences if kind == "def"]
        if not decls:
            continue
        if defs:
            drop.update(decls)
        else:
            drop.update(decls[1:])
            replace[decls[0]] = (
                f"    def {name}("
                "self, *args: Any, **kwargs: Any) -> Any: ..."
            )

    # Pass 2: overlay exact signatures on top of pass-1 spots (either the
    # pythoncode def or the mechanically converted declaration).
    module_overlay_used = False
    module_missed: set[str] = set()
    for (cls, name), signature in _SIGNATURE_LINES.items():
        # Class entries refine the swig stub, where the classes are declared;
        # module entries are tried in both stubs -- SWIG-builtins live in the
        # C extension stub, pythoncode wrappers in the swig stub.
        if cls and is_ext_stub:
            continue
        if cls == "":
            matches = [
                i for i, line in enumerate(lines)
                if line.startswith(f"def {name}(") and i not in drop
            ]
        else:
            occurrences = members.get((cls, name), [])
            matches = [
                idx for idx, kind in occurrences
                if kind == "def" and idx not in drop
            ] + [
                idx for idx, kind in occurrences
                if kind == "decl" and idx not in drop
            ]
        if not matches:
            if cls:
                print(
                    f"note: signature overlay miss: {cls}.{name}",
                    file=sys.stderr,
                )
            else:
                module_missed.add(name)
            continue
        drop.update(matches[1:])
        if cls:
            signature = "    " + signature
        replace[matches[0]] = signature
        if cls == "":
            module_overlay_used = True

    if drop or replace:
        kept = [(i, line) for i, line in enumerate(lines) if i not in drop]
        remap = {old_idx: new_idx for new_idx, (old_idx, _) in enumerate(kept)}
        for old_idx, replacement in replace.items():
            kept[remap[old_idx]] = (old_idx, replacement)
        lines = [line for _, line in kept]

    text = "\n".join(lines) + "\n"
    typing_names = [
        name for name in ("Any", "Callable")
        if re.search(rf"\b{name}\b", text) and f"import {name}" not in text
    ]
    if typing_names:
        text = f"from typing import {', '.join(typing_names)}\n" + text
    if is_ext_stub and module_overlay_used and \
            "from .swig_wiredtiger import" not in text:
        # Classes referenced by module-level signatures (forward refs in the
        # rendered def lines); unused imports are fine in a stub (implicit
        # re-exports).
        text = ("from .swig_wiredtiger import "
                "Connection, FileSystem, KeyProvider, Session\n") + text
    path.write_text(text)
    return module_missed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fork", help="path to the wiredtiger fork (default: auto-detect)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify generated stubs match the fork, exit 1 on drift",
    )
    args = parser.parse_args()

    fork = find_fork(args.fork)
    target_dir = fork / "lang" / "python" / "wiredtiger"
    if not target_dir.is_dir():
        die(f"wiredtiger package directory not found: {target_dir}")

    stubgen = shutil.which("stubgen")
    if stubgen is None:
        die("stubgen not found; install mypy in the current environment")
    stubgen_bin = [stubgen]

    work = Path(tempfile.mkdtemp(prefix="wt-stubs-"))
    try:
        swig_stub = run_stubgen(stubgen_bin, "wiredtiger.swig_wiredtiger", work)
        ext_stub = run_stubgen(stubgen_bin, "wiredtiger._wiredtiger", work)

        patch_relative_import(swig_stub)
        patch_relative_import(ext_stub)
        swig_missed = patch_signatures(swig_stub, is_ext_stub=False)
        ext_missed = patch_signatures(ext_stub, is_ext_stub=True)
        # A module-level name present in neither stub is a real overlay miss;
        # presence in only one is expected (each stub has its own defs).
        for name in swig_missed & ext_missed:
            print(
                f"note: signature overlay miss: <module>.{name}",
                file=sys.stderr,
            )

        header = fork / "src" / "include" / "wiredtiger.h.in"
        if not header.is_file():
            header = fork / "src" / "include" / "wiredtiger.h"
        header_docs: dict[tuple[str, str], list[str]] = {}
        if header.is_file():
            header_docs = build_header_doc_index(header)
        else:
            print(f"note: no C header found ({header}); copydocs unresolved",
                  file=sys.stderr)

        for stub in (swig_stub, ext_stub):
            lines = fix_docstring_bodies(stub.read_text().splitlines())
            lines = resolve_copydocs(lines, header_docs)
            stub.write_text("\n".join(lines) + "\n")

        init_stub = work / "wiredtiger" / "__init__.pyi"
        init_stub.write_text(INIT_PYI_TEMPLATE)
        py_typed = work / "wiredtiger" / "py.typed"
        py_typed.touch()

        outputs = [init_stub, swig_stub, ext_stub, py_typed]

        if args.check:
            drift = [
                name for name in outputs
                if not filecmp.cmp(name, target_dir / name.name, shallow=False)
            ]
            if drift:
                die(
                    "stale stubs for: "
                    + ", ".join(p.name for p in drift)
                    + "; re-run tools/generate_wiredtiger_stubs.py"
                )
            print(f"stubs up to date in {target_dir}")
            return

        for name in outputs:
            shutil.copy2(name, target_dir / name.name)
        print(f"wrote {len(outputs)} files into {target_dir}:")
        for name in outputs:
            print(f"  - {name.name}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
