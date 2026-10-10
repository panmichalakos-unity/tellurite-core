"""Tests for profile / config resolution (file ⊕ overrides ⊕ env precedence)."""

import pytest

from tellurite.config import Profile, load_profile


def test_default_profile_no_file(monkeypatch):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_HTTP_PATH", raising=False)
    prof = load_profile(None)
    assert isinstance(prof, Profile)
    assert prof.name == "default"
    assert prof.cloud == "azure"
    assert prof.lookback_days == 30
    assert prof.show_savings_pct is True


def test_env_fills_connection_gaps(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "env-host")
    monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/env/path")
    prof = load_profile(None)
    assert prof.host == "env-host"
    assert prof.http_path == "/env/path"


def test_file_then_override_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "env-host")
    pf = tmp_path / "profiles.yaml"
    pf.write_text(
        "work:\n  host: file-host\n  cloud: aws\n  lookback_days: 60\n",
        encoding="utf-8",
    )
    # Override beats file; file beats env; untouched file value survives.
    prof = load_profile("work", overrides={"host": "cli-host"}, profile_path=pf)
    assert prof.host == "cli-host"  # override wins
    assert prof.cloud == "aws"  # from file
    assert prof.lookback_days == 60  # from file


def test_unknown_profile_raises(tmp_path):
    pf = tmp_path / "profiles.yaml"
    pf.write_text("work:\n  host: h\n", encoding="utf-8")
    with pytest.raises(KeyError):
        load_profile("nope", profile_path=pf)


def test_workspace_id_derived_from_azure_host(monkeypatch):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    prof = load_profile(None, overrides={"host": "adb-111.2.azuredatabricks.net"})
    assert prof.workspace_id == "111"


def test_workspace_id_explicit_wins_over_host(monkeypatch):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    prof = load_profile(
        None,
        overrides={"host": "adb-111.2.azuredatabricks.net", "workspace_id": "999"},
    )
    assert prof.workspace_id == "999"


def test_workspace_id_none_for_non_azure_host(monkeypatch):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    prof = load_profile(None, overrides={"host": "dbc-abc.cloud.databricks.com"})
    assert prof.workspace_id is None

