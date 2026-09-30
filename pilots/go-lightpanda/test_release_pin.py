"""Keep the OCI source and binary verification bound to the release evidence."""

import json
from pathlib import Path
import unittest


class ReleasePinTest(unittest.TestCase):
    def test_binary_source_survives_nightly_asset_rotation(self):
        pilot = Path(__file__).resolve().parent
        release = json.loads((pilot / "lightpanda-release.json").read_text())
        dockerfile = (pilot / "Dockerfile").read_text()
        integration = (pilot / "integration_test.go").read_text()
        self.assertEqual(release["schema_version"], 2)
        image = release["image"]
        self.assertEqual(image["repository"], "lightpanda/browser")
        self.assertRegex(image["index_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertIn(
            f"ARG LIGHTPANDA_IMAGE={image['repository']}@{image['index_digest']}\n",
            dockerfile,
        )
        self.assertIn("FROM ${LIGHTPANDA_IMAGE} AS lightpanda-source", dockerfile)
        self.assertIn(
            f"COPY --from=lightpanda-source {image['binary_path']} /usr/local/bin/lightpanda",
            dockerfile,
        )
        self.assertEqual(set(release["assets"]), {"amd64", "arm64"})
        for arch, asset in release["assets"].items():
            self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
            self.assertIn(f"{arch}) lightpanda_sha={asset['sha256']} ;;", dockerfile)
            self.assertIn(f'"{arch}": "{asset["sha256"]}"', integration)
            for field in ("image_manifest_digest", "image_binary_layer_digest"):
                self.assertRegex(asset[field], r"^sha256:[0-9a-f]{64}$")
        self.assertIn("sha256sum --check --strict", dockerfile)
        self.assertIn('*) echo "unsupported target architecture:', dockerfile)
        for mutable_source in (
            "releases/assets/", "releases/download/nightly", "lightpanda/browser:nightly"
        ):
            self.assertNotIn(mutable_source, dockerfile)


if __name__ == "__main__":
    unittest.main()
