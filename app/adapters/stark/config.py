from pathlib import Path
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DestinationAccount(BaseModel):
    """Conta destino fixada pelo enunciado do desafio.

    Modelada em vez de lida como dict cru para que um arquivo malformado ou com campo
    faltando quebre na construção do client, e não na hora de transferir dinheiro.
    """

    bank_code: str
    branch_code: str
    account_number: str
    account_type: str
    tax_id: str
    name: str

    @classmethod
    def from_file(cls, path: str) -> "DestinationAccount":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class StarkConfig(BaseSettings):
    """Lê STARK_PROJECT_ID, STARK_PRIVATE_KEY e STARK_ENVIRONMENT do .env."""

    model_config = SettingsConfigDict(
        env_prefix="STARK_",
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore",        
    )

    project_id: str
    private_key_path: str
    environment: str = "sandbox"
    private_key: str | None = None
    destination_account_path: str 


    @field_validator("private_key", mode="before")
    def read_private_key(cls, v, info):
        """Lê a chave privada do arquivo se não estiver no .env."""
        if v is not None:
            return v
        
        path = Path(info.data["private_key_path"])
        return path.read_text(encoding="utf-8")
