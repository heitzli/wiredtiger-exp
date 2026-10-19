# wiredtiger-exp

Type-annotated experiments against a locally built WiredTiger Python
package. `pyproject.toml` installs `wiredtiger` from the sibling fork's
source distribution (`../wiredtiger/lang/python/dist/*.tar.gz`).

Build, stub generation, install, and verification are documented in the
[project root README](../../README.md) — the short cycle:

```bash
uv run --no-sync --with setuptools python \
  wiredtiger/lang/python/setup_pip.py sdist   # from the project root
rm -f uv.lock && uv lock
uv sync --reinstall-package wiredtiger
uv run tools/generate_wiredtiger_stubs.py --check
uv run mypy main.py && uv run python main.py
```

`tools/generate_wiredtiger_stubs.py` regenerates the package stubs from the
installed runtime (`mypy.stubgen --inspect-mode`); it writes into the fork
and supports `--check` (verify stubs match the runtime) and
`--fork <path>` (fork location override).
