import pandas as pd
import polars as pl
import triplets.validate
from triplets.validate import shacl

# Sample data in CIM-like triplet format
data = {
    "ID": ["_1", "_1", "_2", "_2", "_3"],
    "KEY": ["Type", "Name", "Type", "Name", "Type"],
    "VALUE": ["BusbarSection", "Bus 1", "BusbarSection", "Bus 2", "VoltageLevel"]
}

rules = [
    {
        "property": "Name",
        "class": "BusbarSection",
        "min_count": 1,
        "rule_name": "Busbar must have name"
    },
    {
        "property": "Name",
        "class": "VoltageLevel",
        "min_count": 1,
        "rule_name": "VoltageLevel must have name"
    }
]

print("--- Testing Pandas .shacl() ---")
df_pd = pd.DataFrame(data)
# Accessor is registered on import of triplets.validate
violations_pd = df_pd.shacl(rules)
print(f"Pandas Violations Found: {len(violations_pd)}")
print(violations_pd[["ID", "RULE_NAME", "MESSAGE"]] if len(violations_pd) > 0 else "No violations")

print("\n--- Testing Polars .shacl() ---")
df_pl = pl.DataFrame(data)
violations_pl = df_pl.shacl(rules)
print(f"Polars Violations Found: {len(violations_pl)}")
print(violations_pl.select(["ID", "RULE_NAME", "MESSAGE"]) if len(violations_pl) > 0 else "No violations")

print("\n--- Testing functional call triplets.validate.shacl() ---")
violations_func = triplets.validate.shacl(df_pd, rules)
print(f"Functional call Violations Found: {len(violations_func)}")
