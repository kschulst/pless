"""A thin wrapper around the hcloud SDK. No business logic here."""

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
        raise ValueError("HCLOUD_TOKEN is not set — put it in .env and in your password manager.")
    return Client(token=token)


def check_token(client: Client) -> TokenInfo:
    """Verify the token with real but harmless read-only calls."""
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
    raise RuntimeError(f"Server {server.name} has no public IPv4 address.")
