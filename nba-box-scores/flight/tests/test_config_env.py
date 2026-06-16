"""Tests for `build_config_from_env` — the env-only config builder used inside Flights."""

from __future__ import annotations

import pytest

from nba_box_scores_pipeline.config import (
    SEASON_TYPES,
    build_config_from_env,
    current_season_year,
)


class TestRequiredToken:
    def test_missing_token_raises(self):
        with pytest.raises(RuntimeError, match="MOTHERDUCK_TOKEN"):
            build_config_from_env(env={})


class TestDefaults:
    def test_defaults_to_current_season_both_types(self):
        cfg = build_config_from_env(env={"MOTHERDUCK_TOKEN": "t"})
        year = current_season_year()
        assert cfg.seasons == tuple(
            type(cfg.seasons[0])(year=year, type=st) for st in SEASON_TYPES
        )
        assert cfg.delay_ms == 500
        assert cfg.min_delay_ms == 200
        assert cfg.max_delay_ms == 10_000
        assert cfg.force is False
        assert cfg.fill_raw is False
        assert cfg.dry_run is False
        assert cfg.motherduck_token == "t"


class TestOverrides:
    def test_season_override(self):
        cfg = build_config_from_env(env={"MOTHERDUCK_TOKEN": "t", "NBA_INGEST_SEASON": "2020"})
        assert {s.year for s in cfg.seasons} == {2020}
        assert {s.type for s in cfg.seasons} == set(SEASON_TYPES)

    def test_boolean_flags(self):
        cfg = build_config_from_env(env={
            "MOTHERDUCK_TOKEN": "t",
            "NBA_INGEST_FORCE": "1",
            "NBA_INGEST_FILL_RAW": "1",
            "NBA_INGEST_DRY_RUN": "1",
        })
        assert cfg.force and cfg.fill_raw and cfg.dry_run

    def test_boolean_flag_non_one_does_not_enable(self):
        cfg = build_config_from_env(env={
            "MOTHERDUCK_TOKEN": "t",
            "NBA_INGEST_FORCE": "true",  # only "1" enables
            "NBA_INGEST_FILL_RAW": "0",
        })
        assert cfg.force is False
        assert cfg.fill_raw is False

    def test_delay_overrides(self):
        cfg = build_config_from_env(env={
            "MOTHERDUCK_TOKEN": "t",
            "NBA_INGEST_DELAY_MS": "750",
            "NBA_INGEST_MIN_DELAY_MS": "300",
            "NBA_INGEST_MAX_DELAY_MS": "20000",
        })
        assert cfg.delay_ms == 750
        assert cfg.min_delay_ms == 300
        assert cfg.max_delay_ms == 20_000


def _exec_flight_main(name: str):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parent.parent / "flights" / name / "main.py"
    spec = importlib.util.spec_from_file_location(f"{name}_main", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestFlightBootstrappersParse:
    """The flight bootstrappers are stdlib-only (clone + uv sync + subprocess);
    they must parse and expose main()/ENTRYPOINT_COMMAND."""

    def test_nba_nightly_bootstrapper(self):
        m = _exec_flight_main("nba_nightly")
        assert callable(m.main)
        assert m.ENTRYPOINT_COMMAND == "nightly"
        # Deploy defaults that decide whether the registered Flight runs at all.
        assert m.DEFAULT_BRANCH == "main"
        assert str(m.PROJECT_SUBDIR).endswith("nba-box-scores/flight")

    def test_nba_backfill_bootstrapper(self):
        m = _exec_flight_main("nba_backfill")
        assert callable(m.main)
        assert m.ENTRYPOINT_COMMAND == "backfill"
        assert m.DEFAULT_BRANCH == "main"
        assert str(m.PROJECT_SUBDIR).endswith("nba-box-scores/flight")


class TestNightlyWritesProduction:
    """run_nightly must default to the PRODUCTION table set (empty suffix);
    a regression here would silently write box_scores_new and leave the Dive stale."""

    def test_nightly_defaults_to_empty_suffix(self, monkeypatch):
        from nba_box_scores_pipeline import entrypoints

        captured: dict[str, str] = {}
        monkeypatch.setattr(entrypoints, "build_config_from_env", lambda: object())
        monkeypatch.setattr(
            entrypoints, "_run",
            lambda config, *, suffix, label: captured.update(suffix=suffix, label=label),
        )
        entrypoints.run_nightly()
        assert captured["suffix"] == ""  # production, not "_new"


class TestEntrypointDispatch:
    """The package entrypoint module resolves all internal imports and
    routes nightly/backfill — this is what the bootstrapper invokes."""

    def test_commands_registered(self):
        from nba_box_scores_pipeline import entrypoints

        assert set(entrypoints._COMMANDS) == {"nightly", "backfill"}

    def test_unknown_command_exits(self):
        from nba_box_scores_pipeline import entrypoints

        with pytest.raises(SystemExit):
            entrypoints.main(["bogus"])

    def test_no_command_exits(self):
        from nba_box_scores_pipeline import entrypoints

        with pytest.raises(SystemExit):
            entrypoints.main([])

    def test_backfill_requires_season_range(self, monkeypatch):
        from nba_box_scores_pipeline import entrypoints

        monkeypatch.setenv("MOTHERDUCK_TOKEN", "t")
        monkeypatch.delenv("NBA_BACKFILL_START_SEASON", raising=False)
        monkeypatch.delenv("NBA_BACKFILL_END_SEASON", raising=False)
        with pytest.raises(RuntimeError, match="NBA_BACKFILL_START_SEASON"):
            entrypoints.run_backfill()
