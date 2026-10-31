"""Owner-dispatched, bounded transfer of existing Jobseek CI secrets to Doppler."""

import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

PROJECT = "justup-import"
CONFIGS = {
    "repository": "import_repository",
    "Production": "import_production",
    "production-migrations": "import_migrations",
    "production-migration-drift": "import_migration_drift",
}
API = "https://api.doppler.com/v3/configs/config/secrets"


class MigrationError(Exception):
    """A redacted failure that is safe to send to the runner log."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MigrationError("Doppler redirects are refused")


def request(token, config, values=None):
    query = urllib.parse.urlencode({"project": PROJECT, "config": config, "format": "json"})
    url = API + "/download?" + query if values is None else API
    payload = None if values is None else json.dumps({"project": PROJECT, "config": config, "secrets": values}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise MigrationError("Doppler response exceeded the bound")
            return json.loads(data)
    except MigrationError:
        raise
    except Exception:
        # Never send provider response bodies, request payloads or tokens to logs.
        raise MigrationError("Doppler request failed; provider details suppressed") from None


def select_values(source, values, allowlists):
    if source not in CONFIGS or not isinstance(values, dict):
        raise MigrationError("Unsupported migration source")
    expected = set(allowlists[source])
    if any(name.startswith("DOPPLER_") or name.lower() == "github_token" for name in expected):
        raise MigrationError("Bootstrap credentials cannot be migrated")
    missing = [name for name in sorted(expected) if not isinstance(values.get(name), str) or not values[name]]
    if missing:
        raise MigrationError("Missing expected secret names: " + ", ".join(missing))
    return {name: values[name] for name in sorted(expected)}


def migrate(source, token, values, allowlists, transport=request):
    selected = select_values(source, values, allowlists)
    if not token or not token.startswith("dp.st."):
        raise MigrationError("A temporary configuration-scoped service token is required")
    config = CONFIGS[source]
    current = transport(token, config)
    if not isinstance(current, dict) or current.get("DOPPLER_PROJECT") != PROJECT or current.get("DOPPLER_CONFIG") != config:
        raise MigrationError("Import token scope did not match the fixed destination")
    if any(name in current and current[name] != value for name, value in selected.items()):
        raise MigrationError("Existing destination value differs; refusing overwrite")
    transport(token, config, selected)
    verified = transport(token, config)
    if any(verified.get(name) != value for name, value in selected.items()):
        raise MigrationError("Destination verification failed")
    return len(selected)


def main():
    # Pop immediately so child processes cannot inherit the source material.
    raw = os.environ.pop("SOURCE_SECRETS", "")
    token = os.environ.pop("DOPPLER_IMPORT_TOKEN", "")
    source = os.environ.get("MIGRATION_SOURCE", "")
    try:
        values = json.loads(raw)
        allowlists = json.loads(Path(__file__).with_name("doppler-migration-allowlist.json").read_text())
        count = migrate(source, token, values, allowlists)
    except MigrationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print("Migration failed; details suppressed to protect secret values", file=sys.stderr)
        return 1
    print(f"Verified {count} entries in the fixed Doppler import configuration")
    return 0


if __name__ == "__main__":
    sys.exit(main())
