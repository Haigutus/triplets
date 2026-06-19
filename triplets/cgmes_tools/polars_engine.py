"""Native polars engine for cgmes_tools.

Mirrors ``pandas_engine.py`` function-for-function (same names + signatures) so the
dispatcher in ``__init__.py`` can route polars input here instead of round-tripping
through pandas. Most functions orchestrate ``triplets.tools.*`` (``type_tableview``,
``key_tableview``, ``update_triplets_*``), which already dispatch to the polars engine
when ``data`` is a polars DataFrame — so this module is mostly thin glue plus a few
native joins. The pure filename/metadata helpers (no triplet-data arg) are reused
verbatim from ``pandas_engine``.
"""
import math

import polars as pl

from triplets import tools as rdf_parser
from .pandas_engine import (  # pure, engine-agnostic helpers
    default_filename_mask,
    get_metadata_from_filename,
    get_filename_from_metadata,
)


def _u(data):
    """Cast triplet columns to Utf8 (avoids Categorical join/contains pitfalls)."""
    cols = [c for c in ("ID", "KEY", "VALUE", "INSTANCE_ID") if c in data.columns]
    return data.with_columns([pl.col(c).cast(pl.Utf8) for c in cols])


def _empty_triplets():
    return pl.DataFrame(schema={"ID": pl.Utf8, "KEY": pl.Utf8, "VALUE": pl.Utf8, "INSTANCE_ID": pl.Utf8})


def _records(rows):
    """List-of-dicts → triplet frame with a stable column order (empty-safe)."""
    return pl.DataFrame(rows, schema=["ID", "KEY", "VALUE", "INSTANCE_ID"]) if rows else _empty_triplets()


def _pd_merge(left, right, left_on, right_on, suffixes=("_x", "_y"), how="inner"):
    """Mimic ``pandas.merge`` column semantics in polars: columns shared by both frames
    are suffixed on *both* sides (sL/sR), and *both* join keys are retained
    (``coalesce=False``). pandas keeps the differently-named left/right keys; polars would
    otherwise drop the right key."""
    sL, sR = suffixes
    shared = set(left.columns) & set(right.columns)
    lren = {c: f"{c}{sL}" for c in shared} if sL else {}
    rren = {c: f"{c}{sR}" for c in shared} if sR else {}
    L = left.rename(lren)
    R = right.rename(rren)
    lkey = lren.get(left_on, left_on)
    rkey = rren.get(right_on, right_on)
    return L.join(R, left_on=lkey, right_on=rkey, how=how, coalesce=False)


# ── metadata ──────────────────────────────────────────────────────────────────

def get_metadata_from_FullModel(data):
    """Metadata dict for the FullModel instance (KEY→VALUE, 'Type' dropped)."""
    fm = data.filter((pl.col("KEY") == "Type") & (pl.col("VALUE") == "FullModel"))
    uuid = fm["ID"][0]
    obj = data.filter(pl.col("ID") == uuid).select(["KEY", "VALUE"])
    metadata = dict(zip(obj["KEY"].cast(pl.Utf8).to_list(), obj["VALUE"].cast(pl.Utf8).to_list()))
    metadata.pop("Type", None)
    return metadata


def update_FullModel_from_dict(data, metadata, update=True, add=False):
    """Stamp each metadata key onto every FullModel object."""
    fm = _u(data).filter((pl.col("KEY") == "Type") & (pl.col("VALUE") == "FullModel")).select(["ID", "INSTANCE_ID"])
    meta = pl.DataFrame({"KEY": list(metadata.keys()), "VALUE": [str(v) for v in metadata.values()]})
    update_data = fm.join(meta, how="cross").select(["ID", "KEY", "VALUE", "INSTANCE_ID"])
    return data.update_triplets_from_triplets(update_data, update, add)


def update_FullModel_from_filename(data, parser=get_metadata_from_filename, update=False, add=True):
    """Parse each instance's `label` (filename) into metadata and stamp it on its FullModel."""
    d = _u(data)
    records = []
    for label in d.filter(pl.col("KEY") == "label").iter_rows(named=True):
        metadata = parser(label["VALUE"])
        fm = d.filter((pl.col("KEY") == "Type") & (pl.col("VALUE") == "FullModel")
                      & (pl.col("INSTANCE_ID") == label["INSTANCE_ID"]))
        for r in fm.iter_rows(named=True):
            for key, val in metadata.items():
                records.append({"ID": r["ID"], "KEY": key, "VALUE": str(val), "INSTANCE_ID": r["INSTANCE_ID"]})
    return data.update_triplets_from_triplets(_records(records), update, add)


def update_filename_from_FullModel(data, filename_mask=default_filename_mask, filename_key="label"):
    """Regenerate each instance's filename (`label`) from its FullModel metadata."""
    d = _u(data)
    records = []
    for label in d.filter(pl.col("KEY") == filename_key).iter_rows(named=True):
        metadata = get_metadata_from_FullModel(d.filter(pl.col("INSTANCE_ID") == label["INSTANCE_ID"]))
        filename = get_filename_from_metadata(metadata, filename_mask=filename_mask)
        records.append({"ID": label["ID"], "KEY": filename_key, "VALUE": filename, "INSTANCE_ID": label["INSTANCE_ID"]})
    return data.update_triplets_from_triplets(_records(records), add=False)


# ── model inventory ─────────────────────────────────────────────────────────────

def get_loaded_models(data):
    """SV UUID → DataFrame of model parts (ID, PROFILE, INSTANCE_ID) via DependentOn walk."""
    fm = _u(data).filter((pl.col("KEY") == "Model.profile") | (pl.col("KEY") == "Model.DependentOn"))
    sv_ids = fm.filter(pl.col("VALUE") == "http://entsoe.eu/CIM/StateVariables/4/1")["ID"].to_list()

    result = {}
    for sv in sv_ids:
        current = []
        queue = [sv]
        for instance in queue:                                # queue grows as dependencies are found
            profiles = fm.filter((pl.col("ID") == instance) & (pl.col("KEY") == "Model.profile"))
            for p in profiles.iter_rows(named=True):
                current.append({"ID": instance, "PROFILE": p["VALUE"], "INSTANCE_ID": p["INSTANCE_ID"]})
            queue.extend(fm.filter((pl.col("ID") == instance)
                                   & (pl.col("KEY") == "Model.DependentOn"))["VALUE"].to_list())
        result[sv] = (pl.DataFrame(current).unique(maintain_order=True) if current
                      else pl.DataFrame(schema={"ID": pl.Utf8, "PROFILE": pl.Utf8, "INSTANCE_ID": pl.Utf8}))
    return result


def get_model_triplets(data, model_instances_dataframe):
    """Triplets belonging to the given model instances (by INSTANCE_ID)."""
    inst = model_instances_dataframe.select("INSTANCE_ID").unique()
    return data.join(inst, on="INSTANCE_ID", how="inner")


def get_loaded_model_parts(data):
    """FullModel table view of the loaded model parts."""
    return data.type_tableview("FullModel")


def get_EIC_to_mRID_map(data, type):
    """Map IdentifiedObject.energyIdentCodeEic (EIC) → mRID for a given object type."""
    name_map = {"ID": "mRID", "VALUE": "EIC"}
    return (rdf_parser.filter_triplets_by_type(data, type).unique()
            .filter(pl.col("KEY") == "IdentifiedObject.energyIdentCodeEic")
            .select(list(name_map.keys())).rename(name_map))


# ── equipment / statistics ──────────────────────────────────────────────────────

def get_GeneratingUnits(data):
    """Table view of GeneratingUnits (keyed on GeneratingUnit.maxOperatingP)."""
    return data.key_tableview("GeneratingUnit.maxOperatingP")


def count_GeneratingUnit_types(data):
    """Count + total + percentage per GeneratingUnit Type."""
    vc = get_GeneratingUnits(data).group_by("Type").len().rename({"len": "count"})
    total = vc["count"].sum()
    return vc.with_columns([pl.lit(total).alias("TOTAL"),
                            (pl.col("count") / total * 100).alias("%")])


def get_limits(data):
    """Operational limits joined to limit types, terminals and equipment types.

    Reproduces the pandas engine's chained-merge column layout via :func:`_pd_merge`.
    """
    limits = data.type_tableview("OperationalLimitSet", string_to_number=False)
    limits = _pd_merge(limits, data.key_tableview("OperationalLimit.OperationalLimitSet"),
                       left_on="ID", right_on="OperationalLimit.OperationalLimitSet",
                       suffixes=("_OperationalLimitSet", "_OperationalLimit"))
    limits = _pd_merge(limits, data.type_tableview("OperationalLimitType", string_to_number=False),
                       left_on="OperationalLimit.OperationalLimitType", right_on="ID")
    limits = _pd_merge(limits, data.type_tableview("Terminal", string_to_number=False),
                       left_on="OperationalLimitSet.Terminal", right_on="ID", suffixes=("", "_Terminal"))

    fills = [pl.col(c) for c in ("Terminal.ConductingEquipment", "OperationalLimitSet.Equipment")
             if c in limits.columns]
    limits = limits.with_columns(
        (pl.coalesce(fills) if fills else pl.lit(None, dtype=pl.Utf8)).alias("ID_Equipment"))

    types = _u(data).filter(pl.col("KEY") == "Type").select(["ID", "VALUE"])
    limits = _pd_merge(limits, types, left_on="ID_Equipment", right_on="ID", suffixes=("", "_Type"))
    return limits.rename({"VALUE": "Equipment_Type"})


# ── modification ─────────────────────────────────────────────────────────────────

def scale_load(data, load_setpoint, cos_f=None):
    """Scale ConformLoad P/Q so total active power meets ``load_setpoint``."""
    load_data = data.type_tableview("ConformLoad")
    p = load_data["EnergyConsumer.p"].cast(pl.Float64)
    scalable_p = p.sum()
    scalable_q = load_data["EnergyConsumer.q"].cast(pl.Float64).sum()

    if cos_f is None:
        cos_f = math.cos(math.atan(scalable_q / scalable_p))

    total_p = scalable_p + data.type_tableview("NonConformLoad")["EnergyConsumer.p"].cast(pl.Float64).sum()
    new_p = p * (1 + (load_setpoint - total_p) / scalable_p)
    new_q = new_p * math.tan(math.acos(cos_f))
    load_data = load_data.with_columns([new_p.alias("EnergyConsumer.p"), new_q.alias("EnergyConsumer.q")])
    return data.update_triplets_from_tableview(
        load_data.select(["ID", "EnergyConsumer.p", "EnergyConsumer.q"]), update=True, add=False)


def switch_equipment_terminals(data, equipment_id, connected: str = "false"):
    """Set ACDCTerminal.connected for every terminal of the given equipment."""
    if connected not in ("true", "false"):
        raise ValueError("The 'connected' parameter must be 'true' or 'false'.")
    if isinstance(equipment_id, str):
        equipment_id = [equipment_id]

    status_attribute = "ACDCTerminal.connected"
    d = _u(data)
    terminals = (d.filter((pl.col("KEY") == "Terminal.ConductingEquipment")
                          & pl.col("VALUE").is_in(equipment_id)).select(["ID", "KEY", "VALUE"]))
    status = d.filter(pl.col("KEY") == status_attribute).select(["ID", "INSTANCE_ID"])
    terminals = terminals.join(status, on="ID", how="inner").with_columns(
        [pl.lit(status_attribute).alias("KEY"), pl.lit(connected).alias("VALUE")])
    return data.update_triplets_from_triplets(terminals, add=False, update=True)


# ── data quality ─────────────────────────────────────────────────────────────────

def get_dangling_references(data, detailed=False):
    """References whose target ID has no object. detailed=False → counts per KEY."""
    d = _u(data)
    references = d.filter(pl.col("KEY").str.contains(r"\.[A-Z]")).select(["ID", "KEY", "VALUE", "INSTANCE_ID"])
    type_ids = d.filter(pl.col("KEY") == "Type").select("ID")
    dangling = references.join(type_ids, left_on="VALUE", right_on="ID", how="anti")

    if detailed:
        return dangling.rename({"ID": "ID_FROM", "KEY": "KEY_FROM",
                                "VALUE": "VALUE_FROM", "INSTANCE_ID": "INSTANCE_ID_FROM"}).with_columns([
            pl.lit(None, dtype=pl.Utf8).alias("ID_TO"), pl.lit(None, dtype=pl.Utf8).alias("KEY_TO"),
            pl.lit(None, dtype=pl.Utf8).alias("VALUE_TO"), pl.lit(None, dtype=pl.Utf8).alias("INSTANCE_ID_TO"),
            pl.lit("right_only").alias("_merge"),
        ]).select(["ID_TO", "KEY_TO", "VALUE_TO", "INSTANCE_ID_TO",
                   "ID_FROM", "KEY_FROM", "VALUE_FROM", "INSTANCE_ID_FROM", "_merge"])

    # default: value_counts of the dangling reference keys → [KEY, VALUE] (matches the
    # pandas Series once canonicalised: index=KEY, value=count).
    return dangling.group_by("KEY").len().rename({"len": "VALUE"}).select(["KEY", "VALUE"])
