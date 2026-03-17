"""polyphony discussion/flag commands."""

from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.prompt import Prompt

from ..db import connect, fetchone
from ..pipeline.discussion import (
    list_open_flags,
    print_flags_table,
    raise_flag,
    resolve_flag_interactive,
)
from ..utils import build_agent_objects

console = Console()


@click.group()
def discuss():
    """Review flagged cases and facilitate discussion."""


@discuss.command("flags")
@click.option(
    "--status",
    type=click.Choice(["open", "in_discussion", "resolved", "deferred", "all"]),
    default="open",
    show_default=True,
)
@click.pass_context
def flags(ctx, status):
    """List flags requiring attention."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    flag_list = list_open_flags(conn, project["id"], status=status)
    conn.close()
    print_flags_table(flag_list)


@discuss.command("resolve")
@click.argument("flag_id", type=int)
@click.option(
    "--mode",
    type=click.Choice(["supervisor_override", "agent_facilitated", "deferred"]),
    default="agent_facilitated",
    show_default=True,
    help="How to resolve: let agents explain, or supervisor decides directly",
)
@click.pass_context
def resolve(ctx, flag_id, mode):
    """
    Resolve a flagged case.

    In 'agent_facilitated' mode, each agent explains its reasoning before
    you make a final decision. In 'supervisor_override', you decide directly.

    Example:
        polyphony discuss resolve 5 --mode agent_facilitated
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    agent_a, agent_b, _ = build_agent_objects(conn, project["id"])

    resolve_flag_interactive(
        conn=conn,
        project=project,
        flag_id=flag_id,
        agent_a=agent_a,
        agent_b=agent_b,
        mode=mode,
    )
    conn.close()


@discuss.command("raise")
@click.option("--segment", "segment_id", type=int, default=None)
@click.option(
    "--type",
    "flag_type",
    type=click.Choice([
        "ambiguous_segment", "code_overlap", "missing_code",
        "low_confidence", "supervisor_query",
    ]),
    default="supervisor_query",
)
@click.option("--description", "-d", required=True, help="Description of the issue")
@click.pass_context
def raise_cmd(ctx, segment_id, flag_type, description):
    """Raise a flag on a segment or general issue."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    # Find supervisor agent
    sup = fetchone(conn, "SELECT id FROM agent WHERE project_id = ? AND role = 'supervisor'",
                   (project["id"],))
    agent_id = sup["id"] if sup else 1

    flag_id = raise_flag(
        conn=conn,
        project_id=project["id"],
        agent_id=agent_id,
        flag_type=flag_type,
        description=description,
        segment_id=segment_id,
    )
    conn.close()
    console.print(f"[green]Flag #{flag_id} raised.[/]")


@discuss.command("summary")
@click.pass_context
def summary(ctx):
    """Show a summary of all flags and their resolutions."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    from ..db import fetchall
    from rich.table import Table

    counts = fetchall(
        conn,
        """SELECT status, flag_type, COUNT(*) AS n
           FROM flag WHERE project_id = ?
           GROUP BY status, flag_type
           ORDER BY status, flag_type""",
        (project["id"],),
    )
    conn.close()

    table = Table(title="Flag Summary")
    table.add_column("Status")
    table.add_column("Type")
    table.add_column("Count", justify="right")
    for row in counts:
        table.add_row(row["status"], row["flag_type"], str(row["n"]))
    console.print(table)
