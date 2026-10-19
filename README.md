# WiredTiger Python Bindings with Type Stubs

This project builds the WiredTiger Python bindings from source and packages
complete Python type stubs (`.pyi`) so mypy, Zed, and other PEP 561-aware
tools work with the bindings out of the box.

The workflow:

1. Build the WiredTiger Python package as a source distribution.
2. Install it into the experiment project's virtual environment (this
   compiles WiredTiger and the SWIG extension via cmake/ninja).
3. Generate the stubs by introspecting the **installed runtime module** —
   no handwritten stubs, no per-method patching.
4. Rebuild the source distribution so it ships the stubs, and reinstall.
5. Verify with mypy and a runtime smoke test.

The stubs describe the API the way Python users see it: `wiredtiger/__init__.py`
copies all public symbols out of the SWIG-generated `swig_wiredtiger` module at
import time, and large parts of the friendly API (`Cursor.set_key`,
`Cursor.__iter__`, `stat.conn/dsrc/session`, ...) are defined in `%pythoncode`
blocks that SWIG's own `-pyi` output does not model (it emits C proxies like
`_set_key(self, data)`, C-style `next(self, cursor) -> int`, and
`List[...]` returns instead of `Cursor`). Generating from the imported module
makes that mismatch impossible by construction. Only two mundane artifacts are
normalized (see the docstring of `tools/generate_wiredtiger_stubs.py`): the
relative `_wiredtiger` import and the docstring-derived return type of
`wiredtiger_open` (SWIG documents `-> int`, the Python function returns a
`Connection`).

## Prerequisites

- Git
- CMake, Ninja, SWIG, a C/C++ toolchain
- Python 3 (3.13 used here)
- [`uv`](https://docs.astral.sh/uv/)

## Repository layout

```text
exp/                                  <- project root (this README)
├── wiredtiger/                       <- the fork; only setup_pip.py is modified
│   └── lang/python/
│       ├── setup_pip.py              (+: stages stubs and py.typed into the sdist)
│       └── wiredtiger/               <- generated stubs land here (untracked)
│           ├── __init__.pyi
│           ├── swig_wiredtiger.pyi
│           ├── _wiredtiger.pyi
│           └── py.typed
└── wiredtiger-exp/
    └── wiredtiger-exp/
        ├── pyproject.toml            <- wiredtiger source: ../wiredtiger/lang/python/dist/...
        ├── main.py                   <- sample using the public API, type-annotated
        └── tools/
            └── generate_wiredtiger_stubs.py
```

All commands below are run from the project root and use relative paths only.
If you usually work from inside `wiredtiger-exp/wiredtiger-exp`, drop the
leading directory component from each path (`wiredtiger/...` -> `../wiredtiger/...`).

## 1. Build WiredTiger (optional, for iterating on the engine)

The sdist cycle in step 3 compiles everything it needs on its own. A
standalone build is only useful while working on C code:

```bash
cmake -S wiredtiger -B wiredtiger/build -G Ninja \
  -DCMAKE_BUILD_TYPE= -DENABLE_PYTHON=1
cmake --build wiredtiger/build --parallel
ctest --test-dir wiredtiger/build -j"$(sysctl -n hw.ncpu)" -R test_ex_access
```

## 2. Create the environment (first time only)

```bash
cd wiredtiger-exp/wiredtiger-exp
uv sync --no-install-package wiredtiger   # dev deps only; no sdist exists yet
cd ../..
```

## 3. Build the source distribution

```bash
uv run --no-sync --with setuptools python \
  wiredtiger/lang/python/setup_pip.py sdist
```

This stages the tracked fork sources (`git ls-tree`) plus the stub files into
`wiredtiger/lang/python/stage/` and writes
`wiredtiger/lang/python/dist/wiredtiger-<version>.tar.gz`.
`setup_pip.py` in this fork is patched (18 lines) to also stage
`__init__.pyi`, `swig_wiredtiger.pyi`, `_wiredtiger.pyi` and `py.typed` from
`lang/python/wiredtiger/` and to declare them in `MANIFEST.in` and
`package_data`.

The first build ever has no stubs to ship yet — that is fine; install the
distribution anyway (step 4), generate stubs (step 5), then rebuild/reinstall
(steps 6-7).

## 4. Install into the experiment project

`wiredtiger-exp/wiredtiger-exp/pyproject.toml` already points at the local
sdist relatively:

```toml
[tool.uv.sources]
wiredtiger = { path = "../wiredtiger/lang/python/dist/wiredtiger-12.0.0.tar.gz" }
```

```bash
cd wiredtiger-exp/wiredtiger-exp
uv sync          # pip-builds the sdist: cmake/ninja compile the C extension
cd ../..
```

## 5. Generate the stubs

```bash
cd wiredtiger-exp/wiredtiger-exp
uv run tools/generate_wiredtiger_stubs.py
cd ../..
```

The tool runs `mypy.stubgen --inspect-mode` on the installed
`wiredtiger.swig_wiredtiger` (the entire public surface, including
`%pythoncode` wrappers) and `wiredtiger._wiredtiger` (the C extension), applies
the two normalizations, emits `__init__.pyi` (a star re-export mirroring the
runtime `__init__.py`) and `py.typed`, and writes all four files into
`wiredtiger/lang/python/wiredtiger/`. The fork is auto-detected as a sibling
directory; pass `--fork /path/to/wiredtiger` to override.

## 6. Ship the stubs in the sdist

Each rebuild changes the tarball hash, so refresh the lockfile:

```bash
uv run --no-sync --with setuptools python \
  wiredtiger/lang/python/setup_pip.py sdist
cd wiredtiger-exp/wiredtiger-exp
rm -f uv.lock && uv lock          # plain `uv lock --refresh` does not persist the new hash
uv sync --reinstall-package wiredtiger
cd ../..
```

Verify the archive now carries the stubs:

```bash
tar -tzf wiredtiger/lang/python/dist/wiredtiger-12.0.0.tar.gz \
  | grep -E 'wiredtiger-12.0.0/(__init__\.pyi|py\.typed|_?swig_wiredtiger\.pyi)$'
```

Expected output:

```text
wiredtiger-12.0.0/__init__.pyi
wiredtiger-12.0.0/_wiredtiger.pyi
wiredtiger-12.0.0/py.typed
wiredtiger-12.0.0/swig_wiredtiger.pyi
```

## 7. Verify

```bash
cd wiredtiger-exp/wiredtiger-exp

uv run mypy main.py        # Success: no issues found in 1 source file
uv run python main.py      # Got record: key1 : value1
cd ../..
```

`mypy` only honors the shipped stubs because of the `py.typed` marker
(PEP 561); without it, mypy reports "missing library stubs or py.typed
marker" even when a perfect `__init__.pyi` is installed. If you want the
strongest stub-vs-runtime guarantee, run the parity checker:

```bash
cd wiredtiger-exp/wiredtiger-exp
uv run stubtest -m wiredtiger.swig_wiredtiger   # triage SWIG machinery noise
cd ../..
```

## 8. Editor setup (Zed)

1. Open `wiredtiger-exp/wiredtiger-exp` in Zed.
2. Point the Python project at `.venv` (mypy-based tooling picks up the
   installed `wiredtiger/__init__.pyi` automatically thanks to `py.typed`).
3. Reload the project after reinstalling the package.
4. `wiredtiger.Connection`, `wiredtiger.Session`, `wiredtiger.Cursor` and
   methods like `cursor.set_key()` should resolve and navigate.

## Example usage

```python
import os
import wiredtiger
from wiredtiger import wiredtiger_open

os.makedirs("WT_HOME", exist_ok=True)

conn: wiredtiger.Connection = wiredtiger_open("WT_HOME", "create")
session = conn.open_session()
session.create("table:T", "key_format=S,value_format=S")
cursor: wiredtiger.Cursor = session.open_cursor("table:T", None)

cursor.set_key("key1")
cursor.set_value("value1")
cursor.insert()

cursor.reset()
for key, value in cursor:
    print(f"Got record: {key} : {value}")

conn.close()
```

## Troubleshooting

### mypy: "module is installed, but missing library stubs or py.typed marker"

The installed package lacks `py.typed`. Rerun the generator
(`uv run tools/generate_wiredtiger_stubs.py` from
`wiredtiger-exp/wiredtiger-exp`), rebuild the sdist, re lock, reinstall
(steps 6-7).

### The tarball does not contain the stubs

The generator has not been run since the fork was reset/build. Run step 5,
then step 6. (`setup_pip.py` stages stubs straight from
`lang/python/wiredtiger/`, so the files need not be committed/ tracked in
git.)

### uv: "Hash mismatch for wiredtiger"

The lockfile pins the previous tarball hash; every sdist rebuild produces a
new hash. Refresh: `rm -f uv.lock && uv lock`, then `uv sync
--reinstall-package wiredtiger`.

### `from _wiredtiger import ...` is unresolved inside a stub

The generated `swig_wiredtiger.pyi` still contains the absolute import that
the generator normally rewrites. Regenerate (step 5) and confirm the stub
starts with `from ._wiredtiger import`.

### Other members show as `Any`

C function-pointer methods (e.g. `Session.open_cursor`) surface as `Any`
through the class-level builtin wrappers, which is callable and safe. To get
precise return types, extend the normalization in
`tools/generate_wiredtiger_stubs.py` (one entry per factory, the same way
`wiredtiger_open -> Connection` is declared).

## Checklist

- [ ] `wiredtiger/lang/python/setup_pip.py` carries the stub-staging patch.
- [ ] `uv run tools/generate_wiredtiger_stubs.py` writes the 4 stub files.
- [ ] `setup_pip.py sdist` succeeds and the archive contains the 4 stub files.
- [ ] `rm -f uv.lock && uv lock && uv sync --reinstall-package wiredtiger`.
- [ ] `uv run mypy main.py` is clean.
- [ ] `uv run python main.py` prints the record round-trip.
