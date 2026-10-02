from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (and `.env` if present)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    navidrome_url: str = Field(description="Base URL of Navidrome, e.g. http://navidrome:4533")
    navidrome_username: str
    navidrome_password: SecretStr
    navidrome_timeout: float = 30.0

    mcp_auth_token: SecretStr = Field(description="Shared bearer token MCP clients must present")
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000
