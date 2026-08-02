"""Tynn wrapper rundt hcloud-SDK-et. Ingen forretningslogikk her."""

from __future__ import annotations

from dataclasses import dataclass

from hcloud import Client
from hcloud.servers.domain import Server


@dataclass
class TokenInfo:
    server_count: int
    server_names: list[str]
    locations: list[str]


def make_client(token: str) -> Client:
    if not token:
        raise ValueError("HCLOUD_TOKEN er ikke satt — legg det i .env (og Bitwarden).")
    return Client(token=token)


def check_token(client: Client) -> TokenInfo:
    """Verifiser tokenet med reelle, harmløse read-kall."""
    servers = client.servers.get_all()
    locations = client.locations.get_all()
    return TokenInfo(
        server_count=len(servers),
        server_names=[s.name for s in servers],
        locations=[loc.name for loc in locations],
    )


def get_server(client: Client, name: str) -> Server | None:
    return client.servers.get_by_name(name)


def server_ip(server: Server) -> str:
    if server.public_net and server.public_net.ipv4:
        return server.public_net.ipv4.ip
    raise RuntimeError(f"Serveren {server.name} har ingen offentlig IPv4.")
