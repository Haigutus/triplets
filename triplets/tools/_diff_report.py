"""Unified-diff report shared by every engine's ``print_triplets_diff``.

Engines filter by type and collect plain rows; ordering and layout live here so
the output is identical whatever engine computed the diff.
"""
from collections import Counter, defaultdict

SIGN = {"left_only": "-", "right_only": "+"}


def print_diff(diff_rows, related_rows, old_labels, new_labels, context_keys=None, stat=False):
    """Print the diff; returns the number of differing triplets.

    diff_rows : (ID, KEY, VALUE, _merge) with _merge left_only / right_only
    related_rows : (ID, KEY, VALUE, side) for the diff IDs, side old / new, KEY
        "Type" or one of ``context_keys``: object types and context values
    """
    old_types, new_types, context = {}, {}, defaultdict(set)
    for id_, key, value, side in related_rows:
        if key == "Type":
            (old_types if side == "old" else new_types)[id_] = value
        if key in (context_keys or ()):
            context[id_].add((key, value))

    changes = defaultdict(list)
    for id_, key, value, merge in diff_rows:
        changes[id_].append((key, SIGN[merge], value))
    if not changes:
        return 0

    def status(id_):
        if id_ in old_types and id_ not in new_types:
            return "Removed"
        if id_ in new_types and id_ not in old_types:
            return "Added"
        return "Changed"

    def type_of(id_):
        return old_types.get(id_, new_types.get(id_)) or ""

    for label in sorted(old_labels):
        print(f"--- {label}")
    for label in sorted(new_labels):
        print(f"+++ {label}")

    counts = Counter((status(id_), type_of(id_)) for id_ in changes)
    for section in ("Removed", "Added", "Changed"):
        print("")
        print(f"@@ -1,0 +1,0 @@ {section}:")
        for (_, type_), count in sorted((item for item in counts.items() if item[0][0] == section)):
            print(" ", type_, count)

    if not stat:
        for id_ in sorted(changes, key=lambda id_: (type_of(id_), id_)):
            rows = changes[id_]
            changed_keys = {key for key, _, _ in rows}
            rows = rows + [(key, " ", value) for key, value in context[id_] if key not in changed_keys]
            rows.sort(key=lambda row: (row[0], row[1] != "-", row[1] != "+", row[2]))
            removed = sum(sign != "+" for _, sign, _ in rows)
            added = sum(sign != "-" for _, sign, _ in rows)
            print("")
            print(f"@@ -1,{removed} +1,{added} @@ {type_of(id_)} {id_}")
            for key, sign, value in rows:
                print(f"{sign}{key} -> {value}")

    return sum(len(rows) for rows in changes.values())
