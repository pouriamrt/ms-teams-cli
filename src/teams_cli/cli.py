"""Typer root app for the `teams` CLI."""

from __future__ import annotations

import logging
import sys

import typer

from teams_cli.commands import auth as auth_cmds
from teams_cli.commands.chat import chat_app
from teams_cli.commands.meta import json_schema_cmd, version_cmd
from teams_cli.errors import TeamsError

app = typer.Typer(
    name="teams",
    help="Microsoft Teams chats from the terminal.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    verbose: int = typer.Option(0, "-v", "--verbose", count=True, help="-v: INFO, -vv: DEBUG."),
    json_schema: str | None = typer.Option(
        None,
        "--json-schema",
        metavar="NAME",
        help="Print the JSON Schema for a command's --json output.",
    ),
    list_schemas: bool = typer.Option(
        False, "--list", help="With --json-schema: list schema names."
    ),
) -> None:
    """Root callback. Stashes the --json flag and configures logging."""
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    from teams_cli.render.redact import RedactingFilter

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    ctx.obj["verbose"] = verbose

    # Click consumes the next token as the value for --json-schema; recognise
    # `--json-schema --list` as a request to list schema names.
    if json_schema == "--list":
        json_schema = None
        list_schemas = True

    if json_schema is not None or list_schemas:
        json_schema_cmd(json_schema, list_schemas)
        raise typer.Exit(code=0)


app.command("login")(auth_cmds.login_cmd)
app.command("logout")(auth_cmds.logout_cmd)
app.command("whoami")(auth_cmds.whoami_cmd)
app.command("version")(version_cmd)
app.add_typer(chat_app, name="chat")


def main() -> None:
    """Console-script entry point."""
    try:
        app()
    except TeamsError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        sys.exit(exc.exit_code)


if __name__ == "__main__":
    main()
