#!/usr/bin/env python3
"""Apply Ryu's upstream Eventlet compatibility fix to PyPI Ryu 4.34.

Ryu 4.34 imports eventlet.wsgi.ALREADY_HANDLED unconditionally, but Eventlet
removed that private symbol after 0.30.2. Ryu's upstream source now handles
modern Eventlet with getattr(..., None). This script applies only that upstream
change, only to Ryu 4.34, and refuses unknown source instead of guessing.
"""

from __future__ import annotations

import py_compile
import shutil
from importlib import metadata
from pathlib import Path


ORIGINAL_BLOCK = """class _AlreadyHandledResponse(Response):
    # XXX: Eventlet API should not be used directly.
    from eventlet.wsgi import ALREADY_HANDLED
    _ALREADY_HANDLED = ALREADY_HANDLED
"""

UPSTREAM_BLOCK = """class _AlreadyHandledResponse(Response):
    # XXX: Eventlet API should not be used directly.
    # https://github.com/benoitc/gunicorn/pull/2581
    from packaging import version
    import eventlet
    if version.parse(eventlet.__version__) >= version.parse("0.30.3"):
        import eventlet.wsgi
        _ALREADY_HANDLED = getattr(eventlet.wsgi, "ALREADY_HANDLED", None)
    else:
        from eventlet.wsgi import ALREADY_HANDLED
        _ALREADY_HANDLED = ALREADY_HANDLED
"""


def patched_source(source: str) -> tuple[str, bool]:
    """Return upstream-compatible source and whether a change was necessary."""
    if UPSTREAM_BLOCK in source:
        return source, False
    occurrences = source.count(ORIGINAL_BLOCK)
    if occurrences != 1:
        raise RuntimeError(
            "Refusing to patch unexpected ryu/app/wsgi.py source; "
            "expected one Ryu 4.34 ALREADY_HANDLED block, found {}".format(occurrences)
        )
    return source.replace(ORIGINAL_BLOCK, UPSTREAM_BLOCK, 1), True


def installed_wsgi_path() -> Path:
    try:
        distribution = metadata.distribution("ryu")
    except metadata.PackageNotFoundError as error:
        raise RuntimeError("Ryu is not installed in the active Python environment") from error
    installed_version = distribution.version
    if installed_version != "4.34":
        raise RuntimeError(
            "This compatibility patch supports Ryu 4.34 only; found {}".format(installed_version)
        )
    path = Path(distribution.locate_file("ryu/app/wsgi.py")).resolve()
    if not path.is_file():
        raise RuntimeError("Could not locate installed Ryu WSGI module: {}".format(path))
    return path


def main() -> None:
    target = installed_wsgi_path()
    source = target.read_text(encoding="utf-8")
    updated, changed = patched_source(source)
    if not changed:
        print("Ryu 4.34 Eventlet compatibility patch already present: {}".format(target))
        return

    backup = target.with_suffix(".py.medroute-original")
    if not backup.exists():
        shutil.copy2(target, backup)
    target.write_text(updated, encoding="utf-8")
    py_compile.compile(str(target), doraise=True)
    print("Applied upstream ALREADY_HANDLED compatibility patch: {}".format(target))
    print("Original preserved at: {}".format(backup))


if __name__ == "__main__":
    main()

