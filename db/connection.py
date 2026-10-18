"""Multi-tenant FalkorDB connection selector (`codepulse:{tenant_id}`)."""

from __future__ import annotations

import os
from typing import Optional

from falkordb import FalkorDB, Graph

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

DEFAULT_HOST = os.getenv(
    "FALKORDB_HOST",
    "r-6jissuruar.instance-cbx6vbdiq.hc-7up0crkyn.ap-south-1.aws.f2e0a955bb84.cloud",
)
DEFAULT_PORT = int(os.getenv("FALKORDB_PORT", "53319"))
DEFAULT_USERNAME = os.getenv("FALKORDB_USERNAME", "falkordb")
DEFAULT_PASSWORD = os.getenv("FALKORDB_PASSWORD", "yEOhn7yoJm6h")

TENANT_PREFIX = "codepulse:"
FINTECH_PROD_TENANT = "fintech-prod"
SANDBOX_TENANT = "sandbox-tenant"
ALLOWED_TENANTS = (FINTECH_PROD_TENANT, SANDBOX_TENANT)


def get_client(
    host: Optional[str] = None,
    port: Optional[int] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> FalkorDB:
    """Return a connected FalkorDB client instance."""
    resolved_host = host or DEFAULT_HOST
    resolved_port = port or DEFAULT_PORT
    resolved_user = username if username is not None else DEFAULT_USERNAME
    resolved_pass = password if password is not None else DEFAULT_PASSWORD

    # Support passwordless local Docker instances if FALKORDB_PASSWORD=""
    kwargs: dict = {"host": resolved_host, "port": resolved_port}
    if resolved_user:
        kwargs["username"] = resolved_user
    if resolved_pass:
        kwargs["password"] = resolved_pass
    return FalkorDB(**kwargs)


def format_graph_name(tenant_id: str) -> str:
    """Return the canonical FalkorDB multigraph key `codepulse:{tenant_id}`."""
    clean = tenant_id.strip()
    if not clean:
        raise ValueError("tenant_id must not be empty")
    if clean.startswith(TENANT_PREFIX):
        return clean
    return f"{TENANT_PREFIX}{clean}"


def select_tenant_graph(
    tenant_id: str,
    client: Optional[FalkorDB] = None,
) -> Graph:
    """Select the isolated FalkorDB graph for `tenant_id` (`codepulse:{tenant_id}`)."""
    db = client or get_client()
    graph_name = format_graph_name(tenant_id)
    return db.select_graph(graph_name)


def list_tenant_graphs(client: Optional[FalkorDB] = None) -> list[str]:
    """Return all active `codepulse:*` tenant graphs in FalkorDB."""
    db = client or get_client()
    graphs = db.list_graphs()
    return sorted(g for g in graphs if g.startswith(TENANT_PREFIX))
