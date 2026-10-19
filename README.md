# WiredTiger Python Bindings with Type Stubs

Fork of WiredTiger that ships complete Python type stubs (`.pyi`) for the
Python bindings — full signatures and the C docstrings, generated from the
runtime module, PEP 561-compliant (`py.typed`). Stubs are regenerated at
wheel-build time, so they always match the extension you install.

See [BUILDING.md](BUILDING.md) for the build and development manual.

## Install

With [uv](https://docs.astral.sh/uv/):

```bash
uv add "wiredtiger @ git+https://github.com/heitzli/wiredtiger.git#subdirectory=lang/python"
```

Or with pip:

```bash
pip install "wiredtiger @ git+https://github.com/heitzli/wiredtiger.git#subdirectory=lang/python"
```

From a local checkout instead of GitHub:

```bash
uv add ../wiredtiger/lang/python      # or: pip install ../wiredtiger/lang/python
```

Requires cmake, ninja, SWIG and a C/C++ toolchain (the wheel is compiled on
install).
