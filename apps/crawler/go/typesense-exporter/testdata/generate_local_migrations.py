"""Freeze original Alembic SQL for the native deploy consumer; offline only."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path

from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory

root = Path(__file__).resolve().parents[3]
os.environ["LOCAL_DATABASE_URL"] = "postgresql://fixture@localhost/unused"
config = Config(str(root / "src/migrations/alembic.ini"))
script = ScriptDirectory.from_config(config)
revisions = list(reversed(list(script.walk_revisions())))
assert len(revisions) == 40 and script.get_current_head() == "0040"
out = root / "go/typesense-exporter/local-migrations"
out.mkdir(exist_ok=True)
manifest = {
    "schema": "jobseek.local-schema-migrations/v1",
    "head": "0040",
    "source_hashes": {},
    "revisions": [],
}


class StatementBuffer(io.StringIO):
    def __init__(self):
        super().__init__()
        self.statements = []

    def write(self, text):
        statement = text.strip()
        if statement and not statement.startswith("-- Running upgrade"):
            self.statements.append(statement)
        return super().write(text)


original_configure = EnvironmentContext.configure


def native_sql_configure(environment, *args, **kwargs):
    # pgx consumes literal SQL without psycopg pyformat percent escaping.
    kwargs["dialect_opts"] = {"paramstyle": "named"}
    return original_configure(environment, *args, **kwargs)


EnvironmentContext.configure = native_sql_configure

for path in [
    root / "src/migrations/env.py",
    *sorted((root / "src/migrations/versions").glob("*.py")),
    *sorted((root / "src/migrations/sql").glob("*.sql")),
]:
    manifest["source_hashes"][str(path.relative_to(root))] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
for revision in revisions:
    assert revision.revision.isdigit() and len(revision.revision) == 4
    module = revision.module
    special = ""
    if revision.revision in ("0004", "0027"):
        special = "phantom-chunks" if revision.revision == "0004" else "location-name-index"
        module.upgrade = lambda: None
    buf = StatementBuffer()
    config.output_buffer = buf

    def steps(current, context, target=revision.revision):
        return script._upgrade_revs(target, current)

    with (
        contextlib.redirect_stdout(buf),
        EnvironmentContext(
            config,
            script,
            fn=steps,
            as_sql=True,
            starting_rev=revision.down_revision,
            destination_rev=revision.revision,
            dialect_opts={"paramstyle": "named"},
        ),
    ):
        script.run_env()
    sql = buf.getvalue().strip()
    assert sql.startswith("BEGIN;") and sql.endswith("COMMIT;")
    sql = sql.removeprefix("BEGIN;").removesuffix("COMMIT;").strip() + "\n"
    name = revision.revision + ".sql"
    (out / name).write_text(sql)
    entry = {
        "id": revision.revision,
        "previous": revision.down_revision or "",
        "file": name,
        "sha256": hashlib.sha256(sql.encode()).hexdigest(),
        "special": special,
        "statements": buf.statements[1:-1],
        "statements_sha256": hashlib.sha256("\0".join(buf.statements[1:-1]).encode()).hexdigest(),
    }
    assert buf.statements[0] == "BEGIN;" and buf.statements[-1] == "COMMIT;"
    if special == "phantom-chunks":
        tree = ast.parse(Path(revision.path).read_text())
        upgrade = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
        )
        assignment = next(
            n
            for n in upgrade.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "stmt" for t in n.targets)
        )
        query = ast.literal_eval(assignment.value.args[0]).replace(":chunk", "$1")
        (out / "phantom-chunks.sql").write_text(query)
        entry["repair_sha256"] = hashlib.sha256(query.encode()).hexdigest()
        entry.update(chunk=module._CHUNK, max_iterations=module._MAX_ITERATIONS)
    if special == "location-name-index":
        entry["index_name"] = module.INDEX_NAME
    manifest["revisions"].append(entry)
(out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(
    "frozen",
    len(revisions),
    "original migrations; two explicit online-only contracts retained; no database connected",
)
