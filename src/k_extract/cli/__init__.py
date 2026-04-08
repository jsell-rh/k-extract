import click

from k_extract.cli.init import init as init_command


@click.group()
def main() -> None:
    """k-extract: General-purpose knowledge graph extraction framework."""


main.add_command(init_command)
