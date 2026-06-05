# Triplet Query Benchmark: pandas vs Polars

**Date**: 2026-03-26
**Dataset**: 1,146,215 triplet rows (CGMES v2.4.15 RealGrid, 4 files)
**System**: Linux 6.19.8, Python 3.14, pandas 2.2.x, Polars 1.38

## Summary

| Query | pandas | Polars | Speedup | Rows |
|-------|--------|--------|---------|------|
| `type_tableview("ACLineSegment")` | 79.7 ms | 20.5 ms | **3.9x** | 7,561 |
| `key_tableview("IdentifiedObject.name")` | 1,836 ms | 315 ms | **5.8x** | 132,771 |
| `filter_by_type("ACLineSegment")` | 82.9 ms | 9.0 ms | **9.2x** | 113,415 |
| `references_to(id, levels=1)` | 151 ms | 7.7 ms | **19.7x** | 21 |
| `references_from(id, levels=1)` | 40.6 ms | 5.0 ms | **8.2x** | 6 |
| `references_all()` | 231 ms | 54.6 ms | **4.2x** | 242,895 |
| `id_tableview(id)` | 179 ms | 6.1 ms | **29.3x** | 1 |

**Polars is 4-29x faster across all queries.** All result row counts match between implementations.

## Why Polars is Faster

### Lazy evaluation
Polars `.lazy()` builds a query plan that gets optimized before execution. Filter pushdown, projection pruning, and predicate reordering happen automatically.

### Semi joins instead of merge
The pandas implementations use `merge(on="ID")` which creates full join result DataFrames. Polars `join(how="semi")` only checks existence — no intermediate DataFrame allocation.

### Hash-based operations
Polars uses hash tables for joins and `unique()`. pandas `merge` also uses hashing but has more overhead from Python object management and index alignment.

### No column-by-column numeric conversion
The `string_to_number` loop in pandas tries `pd.to_numeric(errors="raise")` per column with Python exception handling. Polars uses `cast(strict=True)` which fails fast in C++.

## Key Patterns in Polars Implementation

### Filter by type (most common operation)
```python
# pandas (82.9 ms)
filter_triplet = data[(data.KEY == type_key) & (data.VALUE == type_name)]
return data.merge(filter_triplet[["ID"]], on="ID", how="inner")

# polars (9.0 ms) — semi join avoids materializing right side
type_ids = data.lazy().filter(
    (pl.col("KEY") == type_key) & (pl.col("VALUE") == type_name)
).select("ID")
return data.lazy().join(type_ids, on="ID", how="semi").collect()
```

### Pivot to table view
```python
# pandas
type_data = pandas.merge(type_id[["ID"]], data, on="ID").drop_duplicates(["ID", "KEY"])
data_view = type_data.pivot(index="ID", columns="KEY")["VALUE"]

# polars — lazy chain, single collect
result = (
    data.lazy()
    .join(type_ids, on="ID", how="semi")
    .unique(subset=["ID", "KEY"])
    .collect()
    .pivot(on="KEY", index="ID", values="VALUE")
)
```

### Reference traversal
```python
# pandas — uses query() string evaluation + merge
object_data = data.query(f"ID == '{reference}'")
reference_data = pandas.merge(reference_column, data,
                              left_on="ID", right_on="VALUE")

# polars — filter + semi join, no string evaluation
object_data = data.filter(pl.col("ID") == reference)
ref_data = data.lazy().join(current_ids.lazy(), left_on="VALUE", right_on="ID", how="semi")
```

## What Could Be Even Faster

- **Pre-built indexes**: Creating a dictionary `{ID: row_indices}` or Polars `group_by("ID")` would make single-ID lookups O(1) instead of O(n) scan
- **Materialized type index**: A separate `{type_name: [IDs]}` lookup table would eliminate the filter step in `type_tableview`
- **Persistent LazyFrame**: Keeping data as a LazyFrame and only collecting at the end would allow Polars to optimize across multiple chained operations
- **Partitioned storage**: For very large datasets, partitioning by INSTANCE_ID or Type would reduce scan scope

## Files

| File | Description |
|------|-------------|
| `triplets/polars_queries.py` | Polars reimplementation of all query functions |
| `benchmark_queries.py` | Query benchmark script (pandas vs Polars) |
| `triplets/rdf_parser.py` | Original pandas implementations (baseline) |
