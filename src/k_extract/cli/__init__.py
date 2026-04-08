import click

from k_extract.cli.init import init as init_command
from k_extract.cli.run import run as run_command


@click.group()
def main() -> None:
    """k-extract: General-purpose knowledge graph extraction framework."""


main.add_command(init_command)
main.add_command(run_command)
