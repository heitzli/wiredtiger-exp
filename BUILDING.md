# Building and Developing the WiredTiger Python Bindings

This project builds the WiredTiger Python bindings from source and packages
complete Python type stubs (`.pyi`) so mypy, Zed, and other PEP 561-aware
tools work with the bindings out of the box.

The workflow:

1. Build the WiredTiger Python package as a source distribution.
2. Install it into the experiment project's virtual environment (this
   compiles WiredTiger and the SWIG extension via cmake/ninja). The stubs
   are **regenerated automatically during the wheel build** by introspecting
   the freshly built extension; the committed stubs are only a fallback
   (e.g. `--no-build-isolation` without mypy, or `WT_SKIP_STUBGEN=1`).
3. Verify with mypy and a runtime smoke test.
4. For iterating on the stub generator itself: regenerate explicitly and
   check the committed copies for drift.

The stubs describe the API the way Python users see it: `wiredtiger/__init__.py`
copies all public symbols out of the SWIG-generated `swig_wiredtiger` module at
import time, and large parts of the friendly API (`Cursor.set_key`,
`Cursor.__iter__`, `stat.conn/dsrc/session`, ...) are defined in `%pythoncode`
blocks that SWIG's own `-pyi` output does not model (it emits C proxies like
`_set_key(self, data)`, C-style `next(self, cursor) -> int`, and
`List[...]` returns instead of `Cursor`). Generating from the imported module
makes that mismatch impossible by construction. The remaining generator
artifacts are normalized (see the docstring of
`wiredtiger/lang/python/tools/wiredtiger_stubgen.py`): the relative
`_wiredtiger` import, a runtime-validated signature overlay table, and
docstring completion via `@copydoc` resolution against the public C header.

## Prerequisites

- Git
- CMake, Ninja, SWIG, a C/C++ toolchain
- Python 3 (3.13 used here)
- [`uv`](https://docs.astral.sh/uv/)

## Repository layout

```text
wiredtiger-exp/                       <- project root (this manual)
├── wiredtiger/                       <- the fork
│   └── lang/python/
│       ├── pyproject.toml            (PEP 517 build config; pins mypy)
│       ├── setup.py                  (shim execing setup_pip.py)
│       ├── setup_pip.py              (sdist staging, build-time stubgen hook)
│       ├── tools/
│       │   └── wiredtiger_stubgen.py (canonical stub generator)
│       └── wiredtiger/               <- committed stubs (fallback)
│           ├── __init__.pyi
│           ├── swig_wiredtiger.pyi
│           ├── _wiredtiger.pyi
│           └── py.typed
└── wiredtiger-exp/
    └── wiredtiger-exp/
        ├── pyproject.toml            <- wiredtiger source: ../wiredtiger/lang/python/dist/...
        ├── main.py                   <- sample using the public API, type-annotated
        └── tools/
            └── generate_wiredtiger_stubs.py  (thin wrapper around the fork tool)
```

All commands below are run from the project root and use relative paths only.
If you usually work from inside `wiredtiger-exp/wiredtiger-exp`, drop the
leading directory component from each path (`wiredtiger/...` -> `../wiredtiger/...`).

## Installing wiredtiger (consumers)

With [uv](https://docs.astral.sh/uv/):

```bash
# from GitHub
uv add "wiredtiger @ git+https://github.com/heitzli/wiredtiger.git#subdirectory=lang/python"

# from a local checkout (working tree)
uv add ../wiredtiger/lang/python

# or from a prebuilt sdist
uv add ../wiredtiger/lang/python/dist/wiredtiger-12.0.0.tar.gz
```

With pip:

```bash
pip install "wiredtiger @ git+https://github.com/heitzli/wiredtiger.git#subdirectory=lang/python"
pip install ../wiredtiger/lang/python
```

All three paths build the wheel through PEP 517 isolation, which installs the
pinned mypy and regenerates the stubs against the extension that was just
compiled; cmake/ninja/SWIG from the system toolchain are required.

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

This stages the tracked fork sources (`git ls-tree`) plus the stub files,
the stub generator (`tools/wiredtiger_stubgen.py`) and `pyproject.toml` into
`wiredtiger/lang/python/stage/` and writes
`wiredtiger/lang/python/dist/wiredtiger-<version>.tar.gz`.

The committed stubs (`lang/python/wiredtiger/*.pyi`, `py.typed`) are shipped
as the fallback; the stub generation itself happens when a consumer builds
the wheel.

## 4. Install into the experiment project

`wiredtiger-exp/wiredtiger-exp/pyproject.toml` already points at the local
sdist relatively:

```toml
[tool.uv.sources]
wiredtiger = { path = "../wiredtiger/lang/python/dist/wiredtiger-12.0.0.tar.gz" }
```

```bash
cd wiredtiger-exp/wiredtiger-exp
rm -f uv.lock && uv lock          # every sdist rebuild changes the tarball hash
uv sync --reinstall-package wiredtiger
cd ../..
```

During this install the wheel build runs the stub generator against the
freshly built extension (`PYTHONPATH=build_lib`), so the installed stubs
always match the compiled C code. Look for
`setup.py: generating type stubs into ...` in the build output.

## 5. Regenerate / check the committed stubs (generator developers)

The exp-side command delegates to the canonical tool in the fork:

```bash
cd wiredtiger-exp/wiredtiger-exp
uv run tools/generate_wiredtiger_stubs.py          # regenerate into the fork
uv run tools/generate_wiredtiger_stubs.py --check  # drift guard, exit 1 on drift
cd ../..
```

The tool runs `stubgen --inspect-mode` on the installed
`wiredtiger.swig_wiredtiger` (the entire public surface, including
`%pythoncode` wrappers) and `wiredtiger._wiredtiger` (the C extension),
applies the normalizations and the signature overlay, resolves `@copydoc`
directives against `src/include/wiredtiger.h.in`, emits `__init__.pyi`
(a star re-export mirroring the runtime `__init__.py`) and `py.typed`, and
writes all four files into `wiredtiger/lang/python/wiredtiger/`. The output
is deterministic: regeneration with the pinned mypy version (2.4.0) is
byte-identical, which is what makes `--check` a usable CI guard.

Commit the refreshed stubs after regenerating.

## 6. Verify

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

## 7. Editor setup (Zed)

1. Open `wiredtiger-exp/wiredtiger-exp` in Zed.
2. Point the Python project at `.venv` (mypy-based tooling picks up the
   installed `wiredtiger/__init__.pyi` automatically thanks to `py.typed`).
3. Reload the project after reinstalling the package.
4. `wiredtiger.Connection`, `wiredtiger.Session`, `wiredtiger.Cursor` and
   methods like `cursor.set_key()` should resolve and navigate.

## Manual test matrix (what to check after changing the build)

1. sdist → `rm -f uv.lock && uv lock` → `uv sync --reinstall-package
wiredtiger` → mypy + runtime green; build log contains
   `generating type stubs`.
2. `uv add ../wiredtiger/lang/python` from a scratch project → mypy strict
   battery green.
3. `uv add "wiredtiger @ git+file:///.../wiredtiger#subdirectory=lang/python"`
   (or the real GitHub URL once pushed) → build from the committed tree,
   mypy strict battery green.
4. `WT_SKIP_STUBGEN=1 uv build --wheel ...` → wheel confirms the committed
   stubs survive as fallback.

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

The installed package lacks `py.typed`. Check that the build log contains
`generating type stubs`; if generation failed the committed stubs (which do
include `py.typed`) are the fallback, so reinstall. Worst case regenerate
step 5, commit, rebuild the sdist, re-lock, reinstall.

### The tarball does not contain the stubs

The committed stubs must exist in `lang/python/wiredtiger/` — run step 5 and
commit. (`setup_pip.py` also dies with `missing type stub` when they are
absent.)

### uv: "Hash mismatch for wiredtiger"

The lockfile pins the previous tarball hash; every sdist rebuild produces a
new hash. Refresh: `rm -f uv.lock && uv lock`, then `uv sync
--reinstall-package wiredtiger`.

### Build log says "stub generation failed, keeping packaged stubs"

mypy/stubgen was unavailable in the build environment (e.g. pip with
`--no-build-isolation`, or a non-PEP 517 flow). The committed stubs are
installed instead. Install mypy into the build environment, or explicitly
skip with `WT_SKIP_STUBGEN=1` to silence the message.

### `stubgen not found` / `No code object available` when running the generator

Run it inside the project environment (`uv run
tools/generate_wiredtiger_stubs.py`) so that mypy >= 2.4 and the installed
`wiredtiger` are both importable in the same interpreter.

### `from _wiredtiger import ...` is unresolved inside a stub

The generated `swig_wiredtiger.pyi` still contains the absolute import that
the generator normally rewrites. Regenerate (step 5) and confirm the stub
starts with `from ._wiredtiger import`.

### Other members show as `Any`

Stubgen cannot see signatures of C function-pointer members, so the generator
converts them mechanically to `def name(self, *args: Any, **kwargs: Any)
-> Any` (callable, honest), and refines the public API — module factories,
`Connection`, `Session`, and `Cursor` — with exact signatures from the
`_SIGNATURE_LINES` table in `lang/python/tools/wiredtiger_stubgen.py`
(return types validated against the runtime: C-backed calls return the WT
error code, `0` == OK; failures raise `WiredTigerError`; positional-only
because SWIG builtins reject keyword arguments). To type another member
precisely, add one table entry and regenerate.
