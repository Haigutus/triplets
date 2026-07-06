"""One-time compile of qlever itself (the static libraries the extension links).

Runs cmake with the two flags that are mandatory for linking qlever's static
archives into a Python extension: position-independent code and
-fno-semantic-interposition (gcc refuses always_inline under plain -fPIC).

Source directory resolution (same order as setup_qlever.py):
    $QLEVER_SRC_DIR → vendor/qlever (submodule) → ../qlever (external checkout)
Build directory: $QLEVER_BUILD_DIR → <src>/build-pic

Usage (the pixi `qlever` environment provides cmake/ninja/compilers and the
C++ deps — boost, icu, openssl, zstd — pinned from conda-forge):

    git submodule update --init --checkout vendor/qlever   # once (update=none)
    pixi run -e qlever build-qlever-lib
"""
import os
import subprocess
import sys


def resolve_qlever_dirs():
    """(source dir, build dir) for the qlever checkout, first candidate that exists."""
    candidates = [os.environ.get("QLEVER_SRC_DIR"),
                  os.path.join("vendor", "qlever"),
                  os.path.join("..", "qlever")]
    for candidate in candidates:
        if candidate and os.path.exists(os.path.join(os.path.expanduser(candidate),
                                                     "src", "libqlever", "Qlever.h")):
            source = os.path.expanduser(candidate)
            build = os.path.expanduser(os.environ.get("QLEVER_BUILD_DIR",
                                                      os.path.join(source, "build-pic")))
            return source, build
    raise RuntimeError(
        "qlever sources not found (need src/libqlever/Qlever.h). Either\n"
        "  git submodule update --init --checkout vendor/qlever\n"
        "or point QLEVER_SRC_DIR at a qlever checkout.")


def main():
    source, build = resolve_qlever_dirs()
    configure = ["cmake", "-S", source, "-B", build, "-GNinja",
                 "-DCMAKE_BUILD_TYPE=Release",
                 "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
                 "-DCMAKE_CXX_FLAGS=-fno-semantic-interposition"]
    compile_ = ["cmake", "--build", build, "--target", "qlever"]
    for command in (configure, compile_):
        print("+", " ".join(command), flush=True)
        subprocess.run(command, check=True)


if __name__ == "__main__":
    sys.exit(main())
