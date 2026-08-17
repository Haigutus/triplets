"""Runtime ABI guard for the pyarrow-linked cython extensions.

Wheels carry a generated ``triplets/_build_info.py`` naming the pyarrow the
extensions were built against; a runtime pyarrow OLDER than that can crash at
call time (the extensions cimport pyarrow's internal Cython API). The guard
raises a clear error instead. Source builds have no _build_info — the local
pyarrow is by definition the one built against — so the check no-ops.
"""


def check_pyarrow():
    try:
        from . import _build_info
    except ImportError:
        return                       # source build — extensions match local pyarrow

    import pyarrow

    def key(version):
        return tuple(int(part) for part in version.split(".")[:3] if part.isdigit())

    if key(pyarrow.__version__) < key(_build_info.BUILD_PYARROW):
        raise ImportError(
            f"triplets' compiled extensions were built against pyarrow "
            f"{_build_info.BUILD_PYARROW}; installed pyarrow {pyarrow.__version__} is older "
            f"and its ABI may crash. Upgrade: pip install 'pyarrow>={_build_info.BUILD_PYARROW}'")
