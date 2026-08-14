from __future__ import annotations

from src.workspace.commands.help import ACTIONS


def test_actions_help_documents_replacement_pagination() -> None:
    assert '"action": "paginate_collect"' in ACTIONS
    assert "replaces the current page" in ACTIONS
    assert "next_selector" in ACTIONS
