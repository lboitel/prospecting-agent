import hmac

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from prospecting.config import get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Security(api_key_header)) -> None:
    expected = get_settings().api_key.get_secret_value()
    if not key or not hmac.compare_digest(key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "clé API invalide")
