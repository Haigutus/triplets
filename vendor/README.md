# Vendor Dependencies

Third-party libraries included as git submodules.

## Libraries

### pugixml

- **Description**: Light-weight, simple and fast XML parser for C++
- **Repository**: https://github.com/zeux/pugixml
- **Version**: v1.15
- **License**: MIT (see `pugixml/LICENSE.md`)
- **Used by**: `setup_cython.py`, `setup_cython_export.py` (Cython extensions that parse/export XML via pugixml C++ API)

### qlever

- **Description**: SPARQL engine that can efficiently index and query very large knowledge graphs
- **Repository**: https://github.com/ad-freiburg/qlever
- **Version**: commit `eeb9fd0` (no tagged releases)
- **License**: Apache 2.0 (see `qlever/LICENSE`)
- **Used by**: `triplets/_native/setup_qlever.py` (Cython extension linking against libqlever for embedded SPARQL)
- **Build requires**: cmake, boost, icu, openssl, zstd, jemalloc

## Initial setup

```bash
git submodule update --init vendor/pugixml vendor/qlever
```

## Updating pugixml

```bash
cd vendor/pugixml
git fetch --tags
git checkout v1.16          # or whichever new tag
cd ../..
git add vendor/pugixml
git commit -m "Update pugixml to v1.16"
```

## Updating qlever

qlever has no tagged releases, so pin to a specific commit:

```bash
cd vendor/qlever
git fetch origin
git checkout <commit-hash>  # pick a known-good commit from main
cd ../..
git add vendor/qlever
git commit -m "Update qlever to <commit-hash>"
```

After updating qlever, rebuild:

```bash
cd vendor/qlever
mkdir -p build && cd build
cmake .. && make -j$(nproc)
```
