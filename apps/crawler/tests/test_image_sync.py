from __future__ import annotations

import csv
from unittest.mock import MagicMock, patch

from src.image_sync import cleanup, update_csv, upload_images


class TestUploadImages:
    def test_no_images_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.image_sync.IMAGES_DIR", tmp_path / "nonexistent")
        result = upload_images()
        assert result == {}

    def test_uploads_logo_and_icon(self, tmp_path, monkeypatch):
        images_dir = tmp_path / "images"
        slug_dir = images_dir / "acme"
        slug_dir.mkdir(parents=True)
        (slug_dir / "logo.svg").write_text("<svg></svg>")
        (slug_dir / "icon.png").write_bytes(b"\x89PNG")

        monkeypatch.setattr("src.image_sync.IMAGES_DIR", images_dir)
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://r2.example.com")
        monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("R2_BUCKET", "test-bucket")
        monkeypatch.setenv("R2_DOMAIN_URL", "https://assets.example.com")

        mock_client = MagicMock()
        with patch("src.image_sync.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_client
            result = upload_images()

        assert result == {
            "acme": {
                "logo_url": "https://assets.example.com/companies/acme/logo.svg",
                "icon_url": "https://assets.example.com/companies/acme/icon.png",
            }
        }
        assert mock_client.upload_file.call_count == 2

    def test_only_logo(self, tmp_path, monkeypatch):
        images_dir = tmp_path / "images"
        slug_dir = images_dir / "acme"
        slug_dir.mkdir(parents=True)
        (slug_dir / "logo.png").write_bytes(b"\x89PNG")

        monkeypatch.setattr("src.image_sync.IMAGES_DIR", images_dir)
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://r2.example.com")
        monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("R2_BUCKET", "test-bucket")
        monkeypatch.setenv("R2_DOMAIN_URL", "https://assets.example.com")

        mock_client = MagicMock()
        with patch("src.image_sync.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_client
            result = upload_images()

        assert "acme" in result
        assert "logo_url" in result["acme"]
        assert "icon_url" not in result["acme"]

    def test_skips_non_directories(self, tmp_path, monkeypatch):
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        (images_dir / "readme.txt").write_text("ignored")

        monkeypatch.setattr("src.image_sync.IMAGES_DIR", images_dir)
        monkeypatch.setenv("R2_ENDPOINT_URL", "https://r2.example.com")
        monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("R2_BUCKET", "test-bucket")
        monkeypatch.setenv("R2_DOMAIN_URL", "https://assets.example.com")

        mock_client = MagicMock()
        with patch("src.image_sync.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_client
            result = upload_images()
        assert result == {}
        mock_client.upload_file.assert_not_called()


class TestUpdateCsv:
    def test_updates_matching_slugs(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "companies.csv"
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["slug", "name", "website", "logo_url", "icon_url", "logo_type"],
            )
            w.writeheader()
            w.writerow(
                {
                    "slug": "acme",
                    "name": "Acme",
                    "website": "https://acme.com",
                    "logo_url": "",
                    "icon_url": "",
                    "logo_type": "wordmark",
                }
            )
            w.writerow(
                {
                    "slug": "other",
                    "name": "Other",
                    "website": "https://other.com",
                    "logo_url": "https://old.com/logo.png",
                    "icon_url": "",
                    "logo_type": "",
                }
            )

        monkeypatch.setattr("src.image_sync.DATA_DIR", tmp_path)

        url_map = {
            "acme": {
                "logo_url": "https://assets.example.com/companies/acme/logo.svg",
                "icon_url": "https://assets.example.com/companies/acme/icon.png",
            }
        }
        update_csv(url_map)

        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))

        assert rows[0]["logo_url"] == "https://assets.example.com/companies/acme/logo.svg"
        assert rows[0]["icon_url"] == "https://assets.example.com/companies/acme/icon.png"
        # Other company untouched
        assert rows[1]["logo_url"] == "https://old.com/logo.png"
        assert rows[1]["icon_url"] == ""


class TestCleanup:
    def test_removes_slug_dirs(self, tmp_path, monkeypatch):
        images_dir = tmp_path / "images"
        slug_dir = images_dir / "acme"
        slug_dir.mkdir(parents=True)
        (slug_dir / "logo.png").write_bytes(b"\x89PNG")

        monkeypatch.setattr("src.image_sync.IMAGES_DIR", images_dir)
        monkeypatch.setattr("src.image_sync.DATA_DIR", tmp_path)
        cleanup(["acme"])

        assert not slug_dir.exists()
        # images_dir itself removed because empty
        assert not images_dir.exists()

    def test_keeps_images_dir_if_not_empty(self, tmp_path, monkeypatch):
        images_dir = tmp_path / "images"
        (images_dir / "acme").mkdir(parents=True)
        (images_dir / "other").mkdir(parents=True)
        (images_dir / "other" / "logo.png").write_bytes(b"\x89PNG")

        monkeypatch.setattr("src.image_sync.IMAGES_DIR", images_dir)
        monkeypatch.setattr("src.image_sync.DATA_DIR", tmp_path)
        cleanup(["acme"])

        assert not (images_dir / "acme").exists()
        assert images_dir.exists()  # still has "other"
