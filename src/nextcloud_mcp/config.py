"""Configuration loaded from environment variables."""

import os
from dataclasses import dataclass, field

from .permissions import PermissionLevel


@dataclass(frozen=True)
class Config:
    """Server configuration from environment variables.

    Required:
        NEXTCLOUD_URL: Base URL of the Nextcloud instance (e.g. http://localhost:8080)
        NEXTCLOUD_USER: Username for authentication
        NEXTCLOUD_PASSWORD: App password for authentication

    Optional:
        NEXTCLOUD_MCP_PERMISSIONS: Permission level — 'read' (default), 'write', or 'destructive'
        NEXTCLOUD_MCP_HOST: Host to bind HTTP server (default: 0.0.0.0)
        NEXTCLOUD_MCP_PORT: Port for HTTP server (default: 8100)
        NEXTCLOUD_MCP_RETRY_MAX: Max retries on 429/503 (default: 3, 0 to disable)
    """

    nextcloud_url: str = field(default="")
    user: str = field(default="")
    password: str = field(default="")
    permission_level: PermissionLevel = field(default=PermissionLevel.READ)
    host: str = field(default="0.0.0.0")
    port: int = field(default=8100)
    retry_max: int = field(default=3)

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        url = os.environ.get("NEXTCLOUD_URL", "").rstrip("/")
        user = os.environ.get("NEXTCLOUD_USER", "")
        password = os.environ.get("NEXTCLOUD_PASSWORD", "")

        perm_str = os.environ.get("NEXTCLOUD_MCP_PERMISSIONS", "read").lower()
        try:
            perm = PermissionLevel(perm_str)
        except ValueError:
            valid = ", ".join(p.value for p in PermissionLevel)
            raise ValueError(f"Invalid NEXTCLOUD_MCP_PERMISSIONS='{perm_str}'. Valid values: {valid}") from None

        host = os.environ.get("NEXTCLOUD_MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("NEXTCLOUD_MCP_PORT", "8100"))
        retry_raw = os.environ.get("NEXTCLOUD_MCP_RETRY_MAX", "3")
        try:
            retry_max = int(retry_raw)
        except ValueError:
            raise ValueError(f"Invalid NEXTCLOUD_MCP_RETRY_MAX='{retry_raw}'. Expected integer >= 0.") from None

        return cls(
            nextcloud_url=url,
            user=user,
            password=password,
            permission_level=perm,
            host=host,
            port=port,
            retry_max=max(0, retry_max),
        )

    def validate(self) -> None:
        """Raise ValueError if required config is missing."""
        missing: list[str] = []
        if not self.nextcloud_url:
            missing.append("NEXTCLOUD_URL")
        if not self.user:
            missing.append("NEXTCLOUD_USER")
        if not self.password:
            missing.append("NEXTCLOUD_PASSWORD")
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}. "
                f"Set them before starting the MCP server."
            )
