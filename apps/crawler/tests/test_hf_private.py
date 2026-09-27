from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.shared.hf_private import require_private_dataset


@pytest.mark.parametrize("private", [False, None, "true", 1])
def test_no_inferred_private_status(private):
    api = Mock()
    api.repo_info.return_value = SimpleNamespace(private=private)
    with pytest.raises(RuntimeError, match="must be private"):
        require_private_dataset(api, "owner/dataset")
    api.update_repo_settings.assert_not_called()
    api.create_repo.assert_not_called()


def test_visibility_lookup_failure_stops_upload():
    api = Mock()
    api.repo_info.side_effect = PermissionError("unavailable")
    with pytest.raises(PermissionError):
        require_private_dataset(api, "owner/dataset")


def test_confirmed_private_allowed():
    api = Mock()
    api.repo_info.return_value = SimpleNamespace(private=True)
    require_private_dataset(api, "owner/dataset")
    api.repo_info.assert_called_once_with(repo_id="owner/dataset", repo_type="dataset")


@pytest.mark.parametrize("uploader", ["trace", "backfill"])
def test_trace_uploads_fail_before_writing_to_public_dataset(monkeypatch, tmp_path, uploader):
    import huggingface_hub

    from src.workspace import trace, trace_backfill

    api = Mock()
    api.repo_info.return_value = SimpleNamespace(private=False)
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda **kw: api)
    monkeypatch.setattr(trace, "_hf_token", lambda: "test")
    monkeypatch.setattr(trace_backfill, "_hf_token", lambda: "test")
    monkeypatch.setattr(trace, "_build_trace", lambda slug: ({"date": "2026-09-27"}, []))
    with pytest.raises(RuntimeError, match="must be private"):
        if uploader == "trace":
            trace.upload_trace_to_hf("test")
        else:
            trace_backfill.upload_and_verify(
                bundle_dir=tmp_path,
                run_id="test",
                repo_id="owner/dataset",
                prefix="training-bundles/v2",
                quality_tier="gold",
            )
    api.upload_file.assert_not_called()
    api.upload_folder.assert_not_called()
