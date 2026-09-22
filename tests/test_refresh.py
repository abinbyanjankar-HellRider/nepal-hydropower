import pytest
from click.testing import CliRunner

from src import cli as cli_module


@pytest.fixture()
def calls(monkeypatch):
    """Replace every stage's work with a recorder so no network or database is touched."""
    log: list[tuple[str, dict]] = []
    reports = cli_module.cli.commands["reports"].commands
    targets = {"backup": cli_module.cli.commands["backup"], "sync": cli_module.cli.commands["sync"],
               "resolve-locations": cli_module.cli.commands["resolve-locations"], "update-news": cli_module.cli.commands["update-news"],
               "validate-data": cli_module.cli.commands["validate-data"], "export": cli_module.cli.commands["export"],
               "financials": reports["financials"], "discover": reports["discover"], "fetch": reports["fetch"], "extract": reports["extract"]}
    for name, cmd in targets.items():
        monkeypatch.setattr(cmd, "callback", lambda _n=name, **kw: log.append((_n, kw)))
    return log


def test_refresh_runs_every_stage_in_order(calls):
    result = CliRunner().invoke(cli_module.cli, ["refresh"])
    assert result.exit_code == 0 and "All stages completed" in result.output
    assert [n for n, _ in calls] == ["backup", "sync", "resolve-locations", "financials", "update-news", "validate-data", "export"]
    assert dict(calls)["sync"] == {"source": "all"}


def test_refresh_with_reports_downloads_annual_before_quarterly_and_extracts_last(calls):
    CliRunner().invoke(cli_module.cli, ["refresh", "--with-reports"])
    names = [n for n, _ in calls]
    assert names.index("discover") < names.index("fetch") < names.index("extract") < names.index("validate-data")
    assert dict(calls)["fetch"]["kind"] == "all"  # `fetch --kind all` does every annual report, then every quarterly one


def test_refresh_skip_network_only_runs_local_stages(calls):
    result = CliRunner().invoke(cli_module.cli, ["refresh", "--skip-network"])
    assert [n for n, _ in calls] == ["backup", "validate-data", "export"] and result.exit_code == 0


def test_a_failing_source_does_not_stop_later_stages_and_exit_code_flags_it(calls, monkeypatch):
    def boom(**kw):
        raise RuntimeError("DoED is down")
    monkeypatch.setattr(cli_module.cli.commands["sync"], "callback", boom)
    result = CliRunner().invoke(cli_module.cli, ["refresh"])
    names = [n for n, _ in calls]
    assert "export" in names and "financials" in names  # everything after the failure still ran
    assert result.exit_code == 1 and "sync (DoED is down)" in result.output
