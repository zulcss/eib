import click

from eib.cli.commands.images import image

@click.group()
def cli():
    pass

#
# Sub-commands
#
cli.add_command(image)
