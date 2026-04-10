from importlib.metadata import version

import click

from k_extract.cli.init import init as init_command
from k_extract.cli.jobs import jobs as jobs_command
from k_extract.cli.run import run as run_command


@click.group()
@click.version_option(version=version("k-extract"), prog_name="k-extract")
def main() -> None:
    """k-extract: General-purpose knowledge graph extraction framework."""


main.add_command(init_command)
main.add_command(jobs_command)
main.add_command(run_command)
