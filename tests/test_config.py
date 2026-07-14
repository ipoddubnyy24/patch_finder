import pytest

from patch_finder import config
from patch_finder.config import (
    ConfigError,
    branch_to_job,
    load_maloo_credentials,
    parse_env_file,
)


def test_parse_env_file_missing(tmp_path):
    assert parse_env_file(tmp_path / "nope.env") == {}


def test_parse_env_file_skips_and_strips(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "# a comment\n"
        "\n"
        "MALOO_USER=alice\n"
        'MALOO_PASS="s3cret"\n'
        "NOTAKEYVALUE\n"
        "QUOTED='v'\n"
    )
    values = parse_env_file(p)
    assert values == {"MALOO_USER": "alice", "MALOO_PASS": "s3cret", "QUOTED": "v"}


def test_load_credentials_from_env():
    creds = load_maloo_credentials(
        environ={"MALOO_USER": "u", "MALOO_PASS": "p", "MALOO_URL": "https://x/"},
    )
    assert creds.username == "u"
    assert creds.password == "p"
    assert creds.base_url == "https://x"  # trailing slash trimmed


def test_load_credentials_falls_back_to_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text("MALOO_USER=fileuser\nMALOO_PASS=filepass\n")
    creds = load_maloo_credentials(environ={}, env_path=p)
    assert creds.username == "fileuser"
    assert creds.base_url == config.DEFAULT_MALOO_URL


def test_load_credentials_env_wins_over_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text("MALOO_USER=fileuser\nMALOO_PASS=filepass\n")
    creds = load_maloo_credentials(environ={"MALOO_USER": "envuser", "MALOO_PASS": "x"}, env_path=p)
    assert creds.username == "envuser"


def test_load_credentials_missing_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_maloo_credentials(environ={}, env_path=tmp_path / "none.env")


def test_load_credentials_default_environ(monkeypatch, tmp_path):
    monkeypatch.setenv("MALOO_USER", "envu")
    monkeypatch.setenv("MALOO_PASS", "envp")
    creds = load_maloo_credentials(env_path=tmp_path / "none.env")
    assert creds.username == "envu"


@pytest.mark.parametrize(
    "branch,job",
    [("b_es6_0", "lustre-b_es6_0"), ("master", "lustre-master"), ("lustre-reviews", "lustre-reviews")],
)
def test_branch_to_job(branch, job):
    assert branch_to_job(branch) == job
