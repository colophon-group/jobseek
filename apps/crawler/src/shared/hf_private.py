"""Fail closed before sending Job Seek content to a Hugging Face dataset."""

from __future__ import annotations


def require_private_dataset(api, repo_id: str) -> None:
    """Require authenticated confirmation; never create or change visibility.

    A missing repo, insufficient permission or failed lookup stops the upload.
    Gating is not private visibility. There is deliberately no public override.
    """
    info = api.repo_info(repo_id=repo_id, repo_type="dataset")
    if getattr(info, "private", None) is not True:
        raise RuntimeError(f"Refusing upload: Hugging Face dataset {repo_id} must be private")
