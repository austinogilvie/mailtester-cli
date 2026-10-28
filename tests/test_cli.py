from typer.testing import CliRunner

from mailtester_cli import __version__
from mailtester_cli.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
