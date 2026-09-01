"""Transport settings. Plain models: no env prefix is imposed on consumers."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from corpus_directus.schema import MANIFESTO_SCHEMA, DirectusSchema

#: The JSON API and the asset endpoint have different shapes of slow: one is a
#: query, the other is tens of megabytes. Sharing one number is the aliasing
#: defect that let the read path inherit the webhook's 30 s budget.
DEFAULT_JSON_TIMEOUT = 10.0
DEFAULT_ASSET_TIMEOUT = 120.0


@dataclass(frozen=True, slots=True)
class Timeouts:
    json: float = DEFAULT_JSON_TIMEOUT
    asset: float = DEFAULT_ASSET_TIMEOUT


DEFAULT_TIMEOUTS = Timeouts()


class DirectusCorpusSettings(BaseModel):
    """Everything needed to build a `DirectusCorpus`, as data."""

    # `schema` shadows a BaseModel attribute, so the field is `schema_` and the
    # alias keeps configuration files reading naturally.
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    base_url: str
    api_key: SecretStr
    timeout_json: float = DEFAULT_JSON_TIMEOUT
    timeout_asset: float = DEFAULT_ASSET_TIMEOUT
    max_ids_per_query: int = 100
    result_webhook: bool = False
    schema_: DirectusSchema = Field(default=MANIFESTO_SCHEMA, alias="schema")

    @property
    def timeouts(self) -> Timeouts:
        return Timeouts(json=self.timeout_json, asset=self.timeout_asset)


class DirectusNotifierSettings(BaseModel):
    """The webhook is a different service: its own URL, token and header."""

    model_config = ConfigDict(frozen=True)

    webhook_url: str
    api_token: SecretStr
    api_key_header: str = "x-api-token"
    timeout: float = 30.0
