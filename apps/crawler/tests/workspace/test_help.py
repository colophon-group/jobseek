from __future__ import annotations

from src.workspace.commands.help import ACTIONS, BROWSER_RESOURCES, INDEX, TOPIC_MAP


def test_actions_help_documents_replacement_pagination() -> None:
    assert '"action": "paginate_collect"' in ACTIONS
    assert "replaces the current page" in ACTIONS
    assert "next_selector" in ACTIONS


def test_browser_resource_help_exposes_anti_bot_safe_profiles_and_ab_runbook() -> None:
    assert "browser-resources" in INDEX
    assert TOPIC_MAP["browser-resources"] == BROWSER_RESOURCES
    assert "resource_policy:none" in BROWSER_RESOURCES
    assert "none        Default." in BROWSER_RESOURCES
    assert "bot_protection:false" in BROWSER_RESOURCES
    assert "blocks no resources" in BROWSER_RESOURCES
    assert "Service workers are never disabled" in BROWSER_RESOURCES
    assert "same egress" in BROWSER_RESOURCES
    assert "inconclusive" in BROWSER_RESOURCES
