"""Tests for FilterConfig loading in src/core/enrich/local.py."""

from __future__ import annotations

import textwrap

import pytest
import yaml

from src.core.enrich.local import FilterConfig, OutputConfig, RequireConfig, load_filter_config


def _write_yaml(tmp_path, content):
    p = tmp_path / "filters.yaml"
    p.write_text(textwrap.dedent(content))
    return str(p)


def test_load_valid_config(tmp_path):
    path = _write_yaml(tmp_path, """
        exclude_title_patterns:
          - senior
          - "sr\\\\."
        require:
          work_permit_support: "yes"
          experience_max: 2
        output:
          limit: 50
    """)
    cfg = load_filter_config(path)
    assert cfg.exclude_title_patterns == ["senior", "sr\\."]
    assert cfg.require.work_permit_support == "yes"
    assert cfg.require.experience_max == 2
    assert cfg.output.limit == 50


def test_load_minimal_config(tmp_path):
    path = _write_yaml(tmp_path, """
        require:
          work_permit_support: "yes"
    """)
    cfg = load_filter_config(path)
    assert cfg.exclude_title_patterns == []
    assert cfg.require.experience_max == 2   # default
    assert cfg.output.limit == 100           # default


def test_load_empty_patterns(tmp_path):
    path = _write_yaml(tmp_path, """
        exclude_title_patterns: []
        require:
          work_permit_support: "yes"
    """)
    cfg = load_filter_config(path)
    assert cfg.exclude_title_patterns == []


def test_missing_require_raises(tmp_path):
    path = _write_yaml(tmp_path, """
        exclude_title_patterns:
          - senior
    """)
    with pytest.raises(Exception):
        load_filter_config(path)


def test_invalid_work_permit_value_raises(tmp_path):
    path = _write_yaml(tmp_path, """
        require:
          work_permit_support: "maybe"
    """)
    with pytest.raises(Exception):
        load_filter_config(path)


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_filter_config("/nonexistent/path/filters.yaml")


def test_exclude_regex_builds_correctly(tmp_path):
    import re
    path = _write_yaml(tmp_path, """
        exclude_title_patterns:
          - senior
          - "vp\\\\b"
          - "head of"
        require:
          work_permit_support: "yes"
    """)
    cfg = load_filter_config(path)
    regex = "|".join(cfg.exclude_title_patterns)
    assert re.search(regex, "Senior Engineer", re.IGNORECASE)
    assert re.search(regex, "VP of Engineering", re.IGNORECASE)
    assert re.search(regex, "Head of Data", re.IGNORECASE)
    assert not re.search(regex, "Software Engineer", re.IGNORECASE)
