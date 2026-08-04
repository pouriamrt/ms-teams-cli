"""`teams version` + `teams --json-schema` helpers."""

from __future__ import annotations

import json

import typer

from teams_cli import __version__
from teams_cli.schemas import get_schema, list_schema_names

meta_app = typer.Typer(name="meta", help="Meta commands.", no_args_is_help=False)


def version_cmd(ctx: typer.Context) -> None:
    """Print the CLI version and target API versions."""
    is_json = bool(ctx.obj and ctx.obj.get("json"))
    payload = {
        "cli_version": __version__,
        "graph_api": "v1.0",
        "chatsvc_api": "ca/v1",
    }
    if is_json:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"teams-cli {__version__}")
        typer.echo("Target APIs: Graph v1.0, chatsvc ca/v1")


def json_schema_cmd(
    name: str | None = typer.Argument(
        None,
        help="Schema name (e.g. chat.list). Omit + use --list to enumerate.",
    ),
    list_names: bool = typer.Option(False, "--list", help="List available schema names."),
) -> None:
    """Print a JSON Schema for a given command output."""
    if list_names:
        for n in list_schema_names():
            typer.echo(n)
        return
    if not name:
        typer.secho("Provide a schema name or --list.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    try:
        schema = get_schema(name)
    except KeyError:
        typer.secho(
            f"Unknown schema: {name}. Use --list to enumerate.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2) from None
    typer.echo(json.dumps(schema, indent=2))
