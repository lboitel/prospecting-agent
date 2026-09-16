from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://prospecting:prospecting@localhost:5432/prospecting"

    # Secret partagé avec n8n (en-tête X-API-Key).
    api_key: SecretStr

    openai_api_key: SecretStr

    # Recherche web + qualification + rédaction.
    model_writer: str = "gpt-5.4-mini"
    # Classification des réponses : volume élevé, tâche simple.
    model_classifier: str = "gpt-5.4-nano"
    research_max_searches: int = 5

    # Offre, ICP, ton : fichiers éditables sans redéployer le code.
    playbook_dir: Path = Path("/app/playbook")

    # Salt pour hacher les emails de la liste d'opposition.
    optout_salt: SecretStr

    # Recherche d'emails des dirigeants (facultatif : sans clé, la collecte s'arrête aux noms).
    hunter_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
