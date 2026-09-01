# isagog-corpus

An abstract newspaper-archive data access layer (`corpus`) and its Directus
specialization (`corpus_directus`). Implements
`docs/20260901corpusdataaccesslayerproposal.md`.

The AI/analysis layer — Temporal workflows and activities, FastAPI services,
eval tools — depends on `corpus` types only. No transport exception, no vendor
filter grammar and no CMS field name crosses the port. A new CMS costs one
adapter plus a passing contract suite; a renamed field on the same CMS costs a
schema constant.

## Layout

```
corpus/                 the port
  models.py             Article, ArticleRef, Edition, EditionRef, AssetRef, ArticlePage
  query.py              ArticleQuery, EditionQuery, ArticleOrder
  errors.py             the CorpusError tree (retryable / retry_after as data)
  capabilities.py       Capability, CorpusCapabilities, CorpusRequirements
  policy.py             ArticlePolicy — consumer-declared data constraints
  normalize.py          CMS hygiene helpers shared by adapters
  cursor.py             opaque pagination cursors
  signals.py            ChangeSignal — inbound evidence, never verdict
  notify.py             ResultNotifier port + NullNotifier
  http_status.py        taxonomy → HTTP status, framework-free
  base.py               the Corpus ABC (template methods live here)
  testing/
    fixtures.py         the canonical seed
    fake.py             FakeCorpus — in-memory reference implementation
    contract.py         CorpusContractSuite — the executable adapter spec

corpus_directus/        the specialization
  schema.py             DirectusSchema + MANIFESTO_SCHEMA
  rows.py               row dict → models (pure)
  compile.py            queries → filter[…]/fields/sort params (pure)
  errors.py             native failure → taxonomy
  client.py             DirectusCorpus(Corpus) — holds an httpx.AsyncClient
  inbound.py            save notification → ChangeSignal
  notifier.py           DirectusFlowNotifier(ResultNotifier)
  settings.py           Timeouts, DirectusCorpusSettings, DirectusNotifierSettings
```

## Using it

```python
from corpus import Capability, CorpusRequirements, ArticleQuery
from corpus_directus import DirectusCorpus, Timeouts

SERVICE_REQUIREMENTS = CorpusRequirements(
    required=frozenset(
        {
            Capability.ARTICLES,
            Capability.EDITIONS,
            Capability.EDITION_PDF,
            Capability.ASSETS,
            Capability.CHANGE_SIGNALS,
        }
    )
)

corpus = DirectusCorpus(
    base_url="https://pulse.example.it",
    api_key=api_key,
    timeouts=Timeouts(json=10.0, asset=120.0),
)
corpus.require(SERVICE_REQUIREMENTS)  # fail fast, names the gap
await corpus.ping()  # fail fast, auth/connectivity

article = await corpus.get_article(article_id)
async for ref in corpus.iter_articles(ArticleQuery(page_size=100)):
    ...
pdf = await corpus.fetch_asset(edition.pdf.id, max_bytes=80_000_000)
```

`fetch_asset` has no default `max_bytes` on purpose: the unguarded whole-PDF
buffer is a defect no caller can now reproduce. Pagination is keyset-based and
the cursor is opaque — consumers cannot tell, and must not care.

### FastAPI wiring

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    corpus = build_corpus(get_settings().corpus)  # the only place a concrete class is named
    corpus.require(SERVICE_REQUIREMENTS)
    await corpus.ping()
    app.state.corpus = corpus
    yield
    await corpus.aclose()


@app.exception_handler(CorpusError)
async def corpus_error_handler(request: Request, exc: CorpusError):
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse({"error": str(exc)}, status_code=http_status_for(exc), headers=headers)
```

`http_status_for` implements the whole table (404 / 422 / 501 / 502 / 503) and
imports no web framework, so it works from FastAPI, Starlette or a plain
handler.

### Temporal consumers

The translation from the taxonomy to `ApplicationError` lives in the consumer,
not here — `temporalio` is banned inside this library by lint. Each activity
catches `CorpusError` and maps it through one table, deriving `non_retryable`
from `err.retryable` and `next_retry_delay` from `err.retry_after`.

### Demo and e2e without a network

```python
from corpus.testing import DEFAULT_SEED, FakeCorpus

corpus = FakeCorpus.from_seed(DEFAULT_SEED)
```

`FakeCorpus` passes the same contract suite as `DirectusCorpus`, so a workflow
test over the fake exercises the same semantics as production.

## Writing a new adapter

1. Implement the mandatory surface: `capabilities`, `get_article`,
   `get_article_ref`, `search_articles`, `ping`.
2. Declare capabilities honestly. Override `get_edition`, `list_editions`,
   `stream_asset` and `parse_change` only for what you declare.
3. Map every native failure into the `CorpusError` tree.
4. Subclass `CorpusContractSuite`, provide `corpus` and `seed` fixtures, and
   make it green. That suite is the definition of done.

The gap between a new backend's capability set and each consumer's
`CorpusRequirements` *is* the migration estimate.

## Development

```bash
uv sync --group dev
uv run pytest --cov                 # 362 tests, contract suite runs twice
uv run ruff check . && uv run pyright
./scripts/check_boundaries.sh       # temporalio / httpx / vendor-vocabulary bans
```

The contract suite also runs a third time against a live instance, behind a
marker — it is what catches a field rename before production does:

```bash
CORPUS_STAGING_BASE_URL=... CORPUS_STAGING_API_KEY=... \
  uv run pytest -m staging --override-ini="addopts="
```

Run `./scripts/check_boundaries.sh <path>...` inside a consumer repo after
migration; a non-zero exit means a fork is growing back.
