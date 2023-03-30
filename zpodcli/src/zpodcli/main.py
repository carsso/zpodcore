from functools import partial

import typer
from rich import print

from zpodcli.cmd import (
    component,
    connect,
    endpoint,
    group,
    library,
    permission,
    pod,
    profile,
    user,
)
from zpodcli.lib.callback import isauthenticated

app = typer.Typer(no_args_is_help=True)


def launch():
    # top level commands
    app.command()(connect.connect)

    # own level commands
    authed_typer = partial(
        app.add_typer,
        callback=isauthenticated,
        no_args_is_help=True,
    )

    authed_typer(group.app, name="group")
    authed_typer(pod.app, name="pod")
    authed_typer(profile.app, name="profile")
    authed_typer(library.app, name="library")
    authed_typer(permission.app, name="permission")
    authed_typer(endpoint.app, name="endpoint")
    authed_typer(component.app, name="component")
    authed_typer(user.app, name="user")

    app()


@app.command()
def version():
    print("0.1.0")


if __name__ == "__main__":
    launch()
