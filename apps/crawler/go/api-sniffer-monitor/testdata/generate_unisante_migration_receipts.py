"""Freeze the original pure Unisanté migration receipt validator."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

source = Path(__file__).resolve().parents[3] / "src/processing/board.py"
module = ast.parse(source.read_text())
names = {
    "_UNISANTE_IDENTITY_MIGRATION",
    "_UNISANTE_IDENTITY_MIGRATION_VERSION",
    "_UNISANTE_IDENTITY_MIGRATION_MAX_ROWS",
}
nodes = [
    node
    for node in module.body
    if (
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)
    )
    or (isinstance(node, ast.FunctionDef) and node.name == "_unisante_receipt_matches")
]
assert len(nodes) == 4
namespace = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
matches = namespace["_unisante_receipt_matches"]
base = {
    "id": "unisante-provider-reference-v1",
    "version": 1,
    "completed_at": "2026-10-10T00:00:00+00:00",
    "updated_count": 1,
    "retired_count": 2,
}
cases = []
for name, receipt in [
    ("normal", base),
    ("null", None),
    ("array", []),
    ("extra", {**base, "extra": 1}),
]:
    cases.append({"name": name, "receipt": receipt, "matches": matches(receipt)})
for key, values in {
    "id": [None, "wrong", 1],
    "version": [None, True, False, 1.0, 2, "1", []],
    "completed_at": [None, "", " ", 1, []],
    "updated_count": [None, True, False, 1.0, -1, 0, 50, 51, "1"],
    "retired_count": [None, True, False, 1.0, -1, 0, 50, 51, "1"],
}.items():
    for index, value in enumerate(values):
        receipt = copy.deepcopy(base)
        receipt[key] = value
        cases.append({"name": f"{key}-{index}", "receipt": receipt, "matches": matches(receipt)})
    receipt = {name: value for name, value in base.items() if name != key}
    cases.append({"name": f"{key}-missing", "receipt": receipt, "matches": matches(receipt)})
target = Path(__file__).with_name("python_unisante_migration_receipts.json")
target.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n")
print(f"Original Unisanté receipt contracts frozen: {len(cases)}")
