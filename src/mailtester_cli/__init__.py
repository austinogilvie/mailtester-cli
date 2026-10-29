"""Command-line client for the MailTester API."""

from importlib.metadata import version

__version__ = version("mailtester-cli")

from mailtester_cli.client import MailTester  # noqa: E402

__all__ = ["MailTester", "__version__"]
