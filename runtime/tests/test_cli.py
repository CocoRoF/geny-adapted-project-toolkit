import pytest

from gapt_runtime import __version__
from gapt_runtime.cli import main


def test_version_command_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["version"])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == __version__


def test_unknown_command_exits_non_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["bogus"])
    assert exc.value.code != 0
