# `Corpus` — an abstract newspaper-archive data access layer, with a Directus specialization

**Status:** proposal. Based on a full inventory of Directus usage across
`memaflow2`, `pdfmanifesto` and `mema-read` (2026-09-01, production instance
`pulse.ilmanifesto.it`).

**Relationship to `2026-08-30-manifesto-directus-library-plan.md`:** that plan
catalogued the duplication (12 forked clients across 8 repos) and designed the
concrete library, but its Decision 2 rejected a CMS-agnostic port on the ground
that *no consumer wanted a different CMS*. That requirement has now changed: the
product goal is that **a customer switching CMS, or a new customer with a
different CMS, costs one adapter — not an archaeology across the AI pipeline**.
This proposal therefore keeps everything that plan got right (the schema map,
the error ladder, the query model, composition-over-inheritance, the parity
gate) and adds the one thing it explicitly deferred: an abstract `Corpus` port
that insulates the LLM/AI analysis layer from the CMS vendor. The concrete plan
becomes the *adapter internals* of this one.

---

## 1. Executive summary

- **Inventory finding:** every piece of Directus knowledge — collection paths,
  field names, the filter grammar, the status literal, the webhook envelope —
  leaks into workflow code, scripts, and eval tools, in at least 6 places
  inside memaflow2 and ~7 more repos outside it. Error handling is a
  copy-pasted 4-way ladder in memaflow2 and a different, partially inconsistent
  scheme in pdfmanifesto. mema-read deliberately reads *derived* stores
  (pgvector, SPARQL/KG) keyed by Directus ids — proof that the "corpus" is
  already bigger than the CMS.
- **Proposal:** one library with two layers:
  - `corpus/` — the abstract port: frozen Pydantic domain models (`Article`,
    `Edition`, `AssetRef`), a vendor-neutral `ArticleQuery`, a single error
    taxonomy (`CorpusError` tree with `retryable` / `retry_after`), a
    **capability declaration** mechanism so each backend states what it
    supports and each consumer declares what it requires (fail-fast at boot),
    and a contract test suite that doubles as the specification a new CMS
    adapter must satisfy.
  - `corpus_directus/` — the specialization: `DirectusCorpus(Corpus)` holding
    an `httpx.AsyncClient` by composition, with all vendor vocabulary confined
    to three small modules (`schema.py`, `rows.py`, `query.py`). Retargeting a
    *different Directus instance* with different field names is a
    `DirectusSchema` object; a *different CMS* is a new adapter passing the
    same contract suite.
- **AI-layer insulation:** workflows, activities, FastAPI services and eval
  tools import `corpus` types only. No `httpx` exception, no `filter[...]`
  string, no `datePublished` ever crosses the port. Temporal retry semantics
  are derived from the taxonomy's `retryable` flag through one translation
  table.
- **Method:** FastAPI + Pydantic v2 + strict TDD. The port ships with
  `FakeCorpus` (in-memory reference implementation) and
  `CorpusContractSuite`; both the fake and the Directus adapter must pass the
  same suite, which is what keeps the fake honest and makes "write a new
  adapter" a checklist instead of a research project.

---

## 2. Inventory

### 2.1 Backend instances and authentication

| Concern | Value | Where seen |
|---|---|---|
| Production instance | `https://pulse.ilmanifesto.it` | memaflow2 settings, suggestions-ui-demo |
| Legacy instance | `https://directus.ilmanifesto.it` | pdfmanifesto `secrets.py` default, memazeit, copertinefull |
| Read auth | static bearer token, `Authorization: Bearer <key>` | all clients |
| Auth probe | `GET /users/me` at client init | memaflow2 `DirectusClient.initialize()` |
| Outbound webhook auth | `x-api-token` header (configurable name), separate token | memaflow2 `notify` |
| Inbound trigger auth | Directus Flow → `POST/PUT /v{1,2}/api/{articles,editions}/{uuid}` on memaflow2's FastAPI | `src/service/starter.py` |

Two instances with the same schema already exist in the wild — the first
concrete argument for a schema-as-data design.

### 2.2 Data entities

**CMS-native (Directus collections):**

| Entity | Fields actually consumed | Notes |
|---|---|---|
| `articles` | `id` (UUID), `slug`, `status`, `datePublished`, `author`, `headline`, `articleKicker`, `articleBody` (HTML), `articleSection.name` | `kicker`/`author` nullable → normalised to `""`; body is HTML, stripped everywhere |
| `editions` | `id` (UUID), `editionDate`, `status`, `slug`, `title`, `editionPdf.pdf` (asset file id), `articles` (o2m, nested rows) | nested articles filtered on `status == "published"` client-side |
| assets | `/assets/{file_id}` → binary | edition PDF, tens of MB |

**Derived stores keyed by Directus ids (the corpus is more than the CMS):**

| Store | Shape | Consumer |
|---|---|---|
| Knowledge graph (Fuseki/QLever) | article node with title/kicker/**summary** (summary exists *only* here, never in Directus), mentions, entities | memaflow2 KG activities; mema-read seed fallback |
| pgvector `mema_article` | `directus_id` PK, `mema_id`, `signature`, `published_day`, `title`, `embedding` (+ `mema_vector_meta`: embedder provider/model/dims) | mema-read similarity API |
| MongoDB `ManifestoPDF` | LLM-extracted structured articles per edition page | pdfmanifesto |

mema-read is explicit about why it does *not* call Directus: the pipeline-generated
`summary` lives only in the graph. Any abstraction must therefore not pretend
the CMS is the only reader-facing source — the port abstracts the **archive
of record** (articles/editions/assets), while enrichment stores stay separate
services with their own interfaces.

**Contract payloads:**

| Direction | Shape | Where |
|---|---|---|
| Outbound webhook | `{"type": "article"\|"edition", "content": {...}}` envelope; content = mema/zeit/summary payloads | `src/models/notifications.py` |
| Inbound save body (proposed, corsie/verbi §7.2) | `{event, publish_date, status, source, content_fingerprint}` | `2026-09-01-articoli-verbi-e-corsie-proposta-unificata.md` |

### 2.3 Access methods catalog

Every distinct operation found, with the Directus grammar it uses:

| # | Operation | Endpoint / grammar | Call sites |
|---|---|---|---|
| 1 | Article by id | `GET /items/articles/{uuid}` — **no `fields` projection** (known defect) | memaflow2 `get_article` |
| 2 | Edition with nested articles | `GET /items/editions/{uuid}?fields=id,editionDate,status,slug,title,editionPdf.pdf,articles.id,articles.status,…` | memaflow2 `get_edition`, `scripts/stamp_edition.py` (sync re-implementation) |
| 3 | Editions by date range | `GET /items/editions?filter[editionDate][_gte]&[_lte]&filter[editionPdf][_null]=false&limit=100&page=N&sort=editionDate` — offset pagination loop | pdfmanifesto `fetch_editions_activity` |
| 4 | Articles by id set | `filter[id][_in]=…` chunked at 100 ids (URL-length limit) | memaflow2 `scripts/run_esperimento.py`, `run_newner_tokens.py` |
| 5 | Article by slug | `filter[slug][_eq]` | memaflow2 `tools/eval/ner.py` |
| 6 | Recent published articles | `sort=-datePublished`, `filter[status][_eq]=published`, `filter[datePublished][_nnull]` | memaflow2 `tools/eval/ner_ab.py` |
| 7 | Edition by exact date | `filter[editionDate][_eq]` | pdfmanifesto scripts, memaflow2 scripts |
| 8 | Asset download (buffered) | `GET /assets/{id}` → whole PDF in memory, 120 s timeout | memaflow2 `fetch_asset` |
| 9 | Asset download (streamed) | `GET /assets/{id}` streamed to disk cache, 300 s timeout | pdfmanifesto `download_pdf_activity` |
| 10 | Auth/health probe | `GET /users/me` | memaflow2 `initialize` |
| 11 | Result write-back | `POST {webhook_url}` with envelope, own auth header | memaflow2 `notify` |
| 12 | Inbound processing trigger | Directus Flow calls memaflow2's FastAPI (`POST`/`PUT` article & edition routes; body-less today, typed body proposed) | `starter.py` |

Observations that shape the design:

- Only **two read patterns** exist in production paths: *fetch one aggregate by
  id* (1, 2, 8, 9) and *list refs by query* (3–7). Everything else is
  contract-plumbing (10–12). The port needs exactly those two shapes plus
  assets and health.
- Pagination exists only in pdfmanifesto and is offset-based; memaflow2
  listings silently truncate (defect). The port must make pagination the
  default, not an option.
- Both asset consumers set special timeouts; one buffers (unguarded, tens of
  MB), one streams. The port must expose streaming with a size guard and let
  buffered fetch be the convenience wrapper.

### 2.4 Error-handling catalog

**memaflow2** (`src/activities/directus.py`) — the most complete taxonomy,
copy-pasted four times (get_article / get_edition / fetch_asset / notify):

| Condition | `ApplicationError.type` | retryable |
|---|---|---|
| `200` with `data: null/[]` | `DIRECTUS_EMPTY_RESPONSE` | no |
| HTTP 4xx (incl. 401/403/404) | `DIRECTUS_HTTP_ERROR` | no |
| HTTP 5xx | `DIRECTUS_HTTP_ERROR` | yes |
| timeout | `DIRECTUS_TIMEOUT` | yes |
| connect error | `DIRECTUS_CONNECTION_ERROR` | yes |
| field validation (`ValueError`/`TypeError`) | `INVALID_ARTICLE_DATA` | no |
| missing key in row | `DIRECTUS_VALIDATION_ERROR` | no |
| webhook variants | `WEBHOOK_{HTTP_ERROR,TIMEOUT,CONNECTION_ERROR}` | same split |

Plus: init-time auth failure → `RuntimeError`; per-row skip-and-log for invalid
nested articles inside an edition (an edition survives one bad article).

**pdfmanifesto** (`temporal/activities/directus.py` + `RetryPolicies.DIRECTUS`):

- 5xx → `DirectusCircuitBreakerException` (retryable; the name is a fossil — it
  is an exception classification, not a breaker anymore).
- 404/403 → `PDFNotFoundException`, listed in `non_retryable_error_types`.
- Retry policy: 3 s → 45 s, ×2.0, 4 attempts.
- **Inconsistency:** other 4xx on download returns
  `DownloadPDFOutput(success=False, …)` instead of raising — two error
  channels for one operation, so callers must check both.
- Per-row skip-and-log for editions with malformed `editionPdf`.

**mema-read:** `ArticleNotFoundError` → HTTP 404; DB health check as boolean;
graceful degradation for pgvector settings (`configure` warns and continues).

**Cross-cutting defects** (verified in code; first catalogued in the 2026-08-30
plan §2.4): no `fields` projection on the hottest call; read path accidentally
running on the webhook's 30 s timeout; webhook sharing the CMS client's pool
and auth; 4× duplicated error ladder; no pagination in memaflow2; whole-PDF
buffering without a size guard; no `Retry-After` handling on 429.

### 2.5 Validation and constraints catalog

What the code enforces today at the CMS boundary, split by whose rule it is:

| Rule | Kind | Where |
|---|---|---|
| `id` must parse as UUID | CMS hygiene | `validate_uuid` |
| `slug` matches `^[a-z0-9]+(?:-[a-z0-9]+)*$` | CMS hygiene | `validate_slug` |
| `datePublished` ISO → normalised `YYYY-MM-DD` | CMS hygiene | `validate_date` |
| HTML stripped from `headline`, `kicker`, `body` | CMS hygiene | `strip_html` |
| `kicker`/`author` null → `""` | CMS hygiene | normalisers |
| `headline`, `body` non-empty after stripping | CMS hygiene | `strip_html` raises |
| body length 300–30 000 chars | **pipeline policy** (memaflow-specific; memazeit and pdfmanifesto have different bounds) | `assert_processable` |
| nested article must be `status == "published"` | query policy | `get_edition` |
| edition must have a PDF | pdfmanifesto policy | `filter[editionPdf][_null]=false` + row check |

The hygiene/policy split matters: hygiene belongs to the adapter (every
consumer wants it), policy belongs to the consumer and must be *declarable*
(§4.4) rather than hard-coded.

---

## 3. Design goals and non-goals

**Goals**

1. The AI/analysis layer (Temporal workflows and activities, FastAPI services,
   eval tools) depends only on `corpus` types: models, queries, errors,
   capabilities. Zero vendor vocabulary above the port.
2. A new CMS = one new adapter package + a passing contract suite. A renamed
   field on the same CMS = a schema-object change in one place.
3. Backends **declare capabilities**; consumers **declare requirements and data
   constraints**; mismatches fail at startup with a message naming the gap —
   never at 2 a.m. mid-edition.
4. One error taxonomy with `retryable`/`retry_after` carried as data, so
   Temporal policies, FastAPI handlers, and plain scripts all derive behavior
   from the same truth table.
5. Full parity with today's observable behavior for memaflow2 (same
   `ApplicationError.type` strings, same retryability), proven by the existing
   test suite plus a replay test.

**Non-goals**

- Abstracting the *enrichment stores* (KG, pgvector, MongoDB). They have their
  own interfaces (`MeMaKnowledgeBase`, `VectorStoreBase`); the corpus port
  covers the archive of record only. `mema-read` keeps doing exactly what it
  does.
- A generic REST/CMS toolkit. `isagog-adapters` already has a generic
  `DirectusAdapter(BaseRESTAdapter)`; this port is *domain-shaped* (newspaper
  archive), not transport-shaped.
- Write access to the CMS. Every consumer today is read-only plus one
  webhook; CRUD on the CMS stays out until a real consumer needs it (it would
  enter as a new capability, not a redesign).
- A TypeScript twin (same position as the 2026-08-30 plan §15.5).

---

## 4. The abstract layer: `corpus/`

Package layout (each file small and single-purpose, per house style):

```
corpus/
  models.py        Article, ArticleRef, Edition, EditionRef, AssetRef, ArticlePage
  query.py         ArticleQuery, EditionQuery, ArticleOrder
  errors.py        CorpusError tree
  capabilities.py  Capability, CorpusCapabilities, CorpusRequirements
  policy.py        ArticlePolicy (consumer-declared data constraints)
  base.py          Corpus ABC (template methods live here)
  signals.py       ChangeSignal, ChangeKind, ActorKind (inbound evidence)
  notify.py        Notifier port: ResultNotifier ABC + NullNotifier
  testing/
    fake.py        FakeCorpus — in-memory reference implementation
    contract.py    CorpusContractSuite — the executable adapter spec
    fixtures.py    canonical rows/payloads
```

Dependencies: `pydantic` only (httpx enters in adapters). `temporalio` is
banned by lint in the whole library, exactly as in the 2026-08-30 plan §11.

### 4.1 Domain models — `models.py`

Frozen Pydantic v2, tuples not lists (immutability rule). Field names are
**carried over unchanged from memaflow2's `ArticleInput`** — they are frozen
Temporal-history contract there, so the abstract model adopting them makes the
memaflow2 migration wire-identical:

```python
class Article(BaseModel):
    """A fully hydrated article from the archive of record.

    Text fields are CMS-hygiene-normalised: HTML stripped, dates YYYY-MM-DD,
    absent kicker/author folded to "". No pipeline policy applied here.
    """

    model_config = ConfigDict(frozen=True)

    id: str  # backend-native id, opaque to consumers
    slug: str
    publish_date: str  # YYYY-MM-DD
    author: str  # "" when absent
    headline: str
    kicker: str  # "" when absent
    body: str  # plain text
    section: str | None = None
    language: str | None = None  # BCP-47; None = backend default


class ArticleRef(BaseModel):
    """Cheap projection for listings and routing — never the full body."""

    model_config = ConfigDict(frozen=True)

    id: str
    slug: str | None = None
    status: str | None = None
    publish_date: str | None = None
    section: str | None = None


class AssetRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    filename: str | None = None
    mime: str | None = None
    size: int | None = None


class Edition(BaseModel):
    """A dated issue of the publication."""

    model_config = ConfigDict(frozen=True)

    id: str
    date: str  # YYYY-MM-DD
    slug: str | None = None
    title: str | None = None
    articles: tuple[Article, ...] = ()
    pdf: AssetRef | None = None


class EditionRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    date: str
    article_count: int | None = None
    pdf: AssetRef | None = None


class ArticlePage(BaseModel):
    """One page of a listing. `next_cursor` is opaque; None = last page."""

    model_config = ConfigDict(frozen=True)

    items: tuple[ArticleRef, ...]
    next_cursor: str | None = None
```

Notes:

- `status` stays an **open string** with a `PUBLISHED = "published"` constant —
  Directus statuses are editorially configurable (open question §10 Q5 of the
  corsie proposal), and other CMSes have their own vocabularies. The adapter
  maps its native literal; the port never enumerates.
- `language` is the one *speculative* field, added because it is the first
  thing a new customer's corpus will differ on and it defaults safely to
  `None`. Nothing else is speculative: every other field is consumed today.

### 4.2 Query model — `query.py`

Derived from the twelve call sites in §2.3, not invented — every production
query is expressible, and each axis maps to a capability:

```python
class ArticleOrder(StrEnum):
    PUBLISH_DATE_DESC = "publish_date_desc"
    PUBLISH_DATE_ASC = "publish_date_asc"


class ArticleQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    ids: tuple[str, ...] = ()  # site 4
    slugs: tuple[str, ...] = ()  # site 5
    edition_id: str | None = None  # site 2 companion
    sections: tuple[str, ...] = ()  # eval sections
    published_from: date | None = None  # sites 3, 7
    published_to: date | None = None
    status: str | None = PUBLISHED  # site 6
    require_publish_date: bool = True  # site 6
    order: ArticleOrder = ArticleOrder.PUBLISH_DATE_DESC
    page_size: int = 100  # transport hint, not a limit


class EditionQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    date_from: date | None = None  # site 3
    date_to: date | None = None
    date_exact: date | None = None  # site 7
    require_pdf: bool = False  # pdfmanifesto's filter
```

The adapter compiles these to its native grammar (`filter[...]` for Directus).
An axis the backend cannot express raises `CapabilityNotSupported` at call
time — and is *detectable before that* via `capabilities` (§4.3), which is
where consumers should check.

### 4.3 Capabilities — `capabilities.py`

The mechanism that makes "declare what is required" real:

```python
class Capability(StrEnum):
    ARTICLES = "articles"  # get_article — the only mandatory one
    ARTICLE_LISTING = "article_listing"  # search/iter + pagination
    ARTICLE_BY_SLUG = "article_by_slug"
    SECTIONS = "sections"
    EDITIONS = "editions"  # get_edition / list_editions
    EDITION_PDF = "edition_pdf"
    ASSETS = "assets"  # fetch/stream binary
    ASSET_STREAMING = "asset_streaming"
    DATE_FILTER = "date_filter"
    CHANGE_SIGNALS = "change_signals"  # inbound save notifications parseable
    RESULT_WEBHOOK = "result_webhook"  # a Notifier is configured


class CorpusCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    supported: frozenset[Capability]
    max_page_size: int = 100
    max_ids_per_query: int = 100  # Directus URL-length chunking surfaces here
    id_format: str = "opaque"  # "uuid" | "int" | "opaque" — documentation, not validation


class CorpusRequirements(BaseModel):
    """What one consumer needs. Checked once at startup."""

    model_config = ConfigDict(frozen=True)

    required: frozenset[Capability]

    def check(self, caps: CorpusCapabilities) -> None:
        missing = self.required - caps.supported
        if missing:
            raise CapabilityNotSupported(
                f"backend lacks required capabilities: {sorted(m.value for m in missing)}"
            )
```

Consumers ship their requirements as a constant next to their settings —
memaflow2 requires `{ARTICLES, EDITIONS, EDITION_PDF, ASSETS, CHANGE_SIGNALS,
RESULT_WEBHOOK}`; pdfmanifesto requires `{EDITIONS, EDITION_PDF, ASSETS,
ASSET_STREAMING, DATE_FILTER}`; an eval tool requires `{ARTICLE_LISTING}`.
Onboarding a new CMS starts by writing its capability set and diffing it
against each consumer's requirements — the gap list *is* the migration
estimate.

### 4.4 Consumer data constraints — `policy.py`

The hygiene/policy split from §2.5, made declarative. Hygiene lives in
adapters; policy is data owned by the consumer and applied at its boundary:

```python
class ArticlePolicy(BaseModel):
    """Pipeline-specific processability constraints. NOT enforced by the
    corpus — each consumer applies its own at its boundary."""

    model_config = ConfigDict(frozen=True)

    required_fields: frozenset[str] = frozenset({"headline", "body", "publish_date"})
    min_body_chars: int | None = None
    max_body_chars: int | None = None

    def check(self, article: Article) -> None:
        for name in self.required_fields:
            if not getattr(article, name):
                raise InvalidDocument(f"required field {name!r} is empty", kind="missing_field")
        n = len(article.body)
        if self.min_body_chars is not None and n < self.min_body_chars:
            raise InvalidDocument(f"body too short ({n})", kind="bad_value")
        if self.max_body_chars is not None and n > self.max_body_chars:
            raise InvalidDocument(f"body too long ({n})", kind="bad_value")
```

memaflow2's `assert_processable` becomes
`ArticlePolicy(min_body_chars=300, max_body_chars=30_000)` in its own config;
memazeit and pdfmanifesto declare theirs. The corpus never rejects an article a
different pipeline could use.

### 4.5 Error taxonomy — `errors.py`

Vendor-neutral, deliberately isomorphic to the 2026-08-30 plan's
`DirectusError` ladder so the memaflow translation table is a rename:

```python
class CorpusError(Exception):
    retryable: bool = False
    retry_after: float | None = None


class CorpusConfigError(CorpusError): ...  # bad base_url, missing key


class CorpusAuthError(CorpusError): ...  # 401/403


class CapabilityNotSupported(CorpusError): ...  # port misuse, non-retryable


class DocumentNotFound(CorpusError):  # source: "status" (404) | "empty" (200+null)
    source: Literal["status", "empty"]


class InvalidDocument(CorpusError):  # kind: "missing_field" | "bad_value"
    kind: Literal["missing_field", "bad_value"]


class CorpusUnavailable(CorpusError):  # retryable=True
    kind: Literal["timeout", "connect", "http"]  # http = 5xx


class CorpusRateLimited(CorpusError):  # retryable=True, retry_after from header
    ...
```

Rules:

- Adapters map **every** native failure (transport exceptions, status codes,
  200-with-empty-body, malformed rows) into this tree. A raw `httpx` exception
  escaping an adapter is a contract-suite failure.
- `DocumentNotFound.source` and `InvalidDocument.kind` exist to reproduce
  memaflow2's current distinct `type=` strings exactly (parity, §8).
- The Temporal translation table (one place, in memaflow2, not in the library):

| Corpus error | `ApplicationError.type` | non_retryable |
|---|---|---|
| `DocumentNotFound(source="empty")` | `DIRECTUS_EMPTY_RESPONSE` | True |
| `DocumentNotFound(source="status")` / `CorpusAuthError` | `DIRECTUS_HTTP_ERROR` | True |
| `InvalidDocument(kind="bad_value")` | `INVALID_ARTICLE_DATA` | True |
| `InvalidDocument(kind="missing_field")` | `DIRECTUS_VALIDATION_ERROR` | True |
| `CorpusUnavailable(kind="timeout")` | `DIRECTUS_TIMEOUT` | False |
| `CorpusUnavailable(kind="connect")` | `DIRECTUS_CONNECTION_ERROR` | False |
| `CorpusUnavailable(kind="http")` | `DIRECTUS_HTTP_ERROR` | False |
| `CorpusRateLimited` | `DIRECTUS_HTTP_ERROR` + `next_retry_delay` (cap 30 s) | False |

pdfmanifesto's fossil names (`DirectusCircuitBreakerException`,
`PDFNotFoundException`) map to `CorpusUnavailable(kind="http")` and
`DocumentNotFound` when it adopts, and its two-channel download error
(§2.4) collapses into the single raise channel.

### 4.6 The `Corpus` ABC — `base.py`

An ABC rather than a Protocol, because template methods carry real shared
behavior (pagination, capability gating, buffered-from-streamed):

```python
class Corpus(ABC):
    """Read-side port over a newspaper/archive backend."""

    # --- identity ---------------------------------------------------------
    @property
    @abstractmethod
    def capabilities(self) -> CorpusCapabilities: ...

    def require(self, requirements: CorpusRequirements) -> None:
        requirements.check(self.capabilities)  # concrete, final

    # --- articles ---------------------------------------------------------
    @abstractmethod
    async def get_article(self, article_id: str) -> Article: ...

    @abstractmethod
    async def get_article_ref(self, article_id: str) -> ArticleRef: ...

    @abstractmethod
    async def search_articles(
        self, query: ArticleQuery, cursor: str | None = None
    ) -> ArticlePage: ...

    async def iter_articles(self, query: ArticleQuery) -> AsyncIterator[ArticleRef]:
        """Concrete: drains search_articles pages. Adapters override only if
        they have a cheaper native scan."""
        cursor: str | None = None
        while True:
            page = await self.search_articles(query, cursor)
            for ref in page.items:
                yield ref
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    # --- editions (capability-gated: default raises) ----------------------
    async def get_edition(self, edition_id: str) -> Edition:
        raise CapabilityNotSupported("editions")

    async def list_editions(self, query: EditionQuery) -> tuple[EditionRef, ...]:
        raise CapabilityNotSupported("editions")

    # --- assets -----------------------------------------------------------
    def stream_asset(self, asset_id: str) -> AsyncIterator[bytes]:
        raise CapabilityNotSupported("assets")

    async def fetch_asset(self, asset_id: str, *, max_bytes: int) -> bytes:
        """Concrete: buffers stream_asset with a hard size guard."""
        chunks, total = [], 0
        async for chunk in self.stream_asset(asset_id):
            total += len(chunk)
            if total > max_bytes:
                raise InvalidDocument(
                    f"asset {asset_id} exceeds {max_bytes} bytes", kind="bad_value"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    # --- health -----------------------------------------------------------
    @abstractmethod
    async def ping(self) -> None: ...

    async def aclose(self) -> None: ...
```

Design points:

- **Mandatory surface is tiny** (get_article, get_article_ref,
  search_articles, ping, capabilities): the minimum a corpus must be. Editions
  and assets are opt-in via capability + override — a web-native archive with
  no print edition is a first-class corpus, not a pile of NotImplementedError.
- `fetch_asset` requiring `max_bytes` as keyword-only closes the unguarded-PDF
  defect *structurally*: no caller can forget it.
- Pagination is cursor-based and opaque. The Directus adapter uses keyset
  (`datePublished,id`) — offset paging over a collection being written during
  an edition evening skips and repeats rows; another CMS may hand back its own
  token. Consumers cannot tell and must not care.

### 4.7 Inbound change signals — `signals.py`

The corsie proposal's `IngestSignal`, vendor-neutralized and adopted as part of
the port, because "the CMS tells us something changed" is exactly the kind of
vendor contract the AI layer must not parse itself:

```python
class ChangeKind(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    PUBLISH = "publish"
    UNKNOWN = "unknown"


class ActorKind(StrEnum):
    EDITOR = "editor"
    BULK = "bulk"
    IMPORT = "import"
    MIGRATION = "migration"
    API = "api"
    UNKNOWN = "unknown"


class ChangeSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    article_id: str
    change: ChangeKind = ChangeKind.UNKNOWN
    actor: ActorKind = ActorKind.UNKNOWN
    publish_date: date | None = None
    status: str | None = None
    fingerprint: str | None = None
    received_at: datetime
    raw: Mapping[str, Any] = {}  # verbatim, for audit
```

The three security properties from the corsie design are preserved verbatim and
are now *port invariants*, tested in the contract suite:

1. no `priority`/`lane` field exists — **evidence, never verdict**;
2. every default is the most conservative value (`UNKNOWN` demotes);
3. a body-less request parses to a fully-formed signal — tolerant parsing
   never raises, unknown vocabulary degrades to `UNKNOWN`.

Each adapter ships `parse_change(article_id, body) -> ChangeSignal` translating
its native vocabulary (`items.create`, `"editor"`, …). Routing decisions
(Express/Backlog, budgets) remain consumer policy, above the port.

### 4.8 Outbound results — `notify.py`

A separate small port, never a method on `Corpus` (different service, auth,
pool, SLA — defect 3 of §2.4):

```python
class ResultNotifier(ABC):
    @abstractmethod
    async def publish(self, kind: str, content: Mapping[str, Any]) -> PublishResult: ...
    @abstractmethod
    async def ping(self) -> None: ...


class NullNotifier(ResultNotifier):
    """Same surface, delivers nothing, returns delivered=False."""
```

`DirectusFlowNotifier` (in the adapter package) owns the
`{"type": …, "content": …}` envelope and the `x-api-token` header. memaflow2's
domain payloads (`ZeitPayload`, `EditionSummary`, …) never enter the library;
`to_webhook_payload()` reduces to producing `content`.

---

## 5. The Directus specialization: `corpus_directus/`

```
corpus_directus/
  schema.py      DirectusSchema + MANIFESTO_SCHEMA constant
  rows.py        row dict → Article / ArticleRef / Edition (pure functions)
  compile.py     ArticleQuery/EditionQuery → params dict (filter[…]/fields/sort)
  client.py      DirectusCorpus(Corpus) — holds httpx.AsyncClient
  inbound.py     save-notification body → ChangeSignal
  notifier.py    DirectusFlowNotifier(ResultNotifier)
  settings.py    DirectusCorpusSettings (plain BaseModel, no env prefix imposed)
```

### 5.1 Schema as data

All vendor vocabulary in one frozen object; the *il manifesto* instance is a
constant, and a second Directus with different field names is another constant,
not another codebase:

```python
class DirectusSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    articles_collection: str = "articles"
    editions_collection: str = "editions"
    article_fields: Mapping[str, str] = {
        "id": "id",
        "slug": "slug",
        "status": "status",
        "publish_date": "datePublished",
        "author": "author",
        "headline": "headline",
        "kicker": "articleKicker",
        "body": "articleBody",
        "section": "articleSection.name",
    }
    edition_fields: Mapping[str, str] = {
        "id": "id",
        "date": "editionDate",
        "status": "status",
        "slug": "slug",
        "title": "title",
        "pdf": "editionPdf.pdf",
    }
    published_status: str = "published"


MANIFESTO_SCHEMA = DirectusSchema()
```

`rows.py` and `compile.py` read field names *only* through the schema. The
explicit `fields=` projection on every request (closing defect 1) is generated
from these maps, so the projection and the parser cannot drift apart.

### 5.2 The client

```python
@final
class DirectusCorpus(Corpus):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        schema: DirectusSchema = MANIFESTO_SCHEMA,
        timeouts: Timeouts = Timeouts(json=10.0, asset=120.0),
        limits: httpx.Limits | None = None,
    ) -> None: ...
```

Transport policy owned here and only here (all carried over from the
2026-08-30 plan §5.2, unchanged in substance):

- composition: *holds* an `httpx.AsyncClient`; `@final`; does **not** subclass
  it — the single structural rule that stops consumers reaching through into
  the query DSL, which is how the 12 forks happened;
- `AsyncHTTPTransport(retries=0)` — the **caller owns retries** (Temporal in
  memaflow/pdfmanifesto, a loop elsewhere); transport-level retries would
  double-count attempts and hide failures from retry policies;
- typed `Timeouts(json=…, asset=…)` (closes the timeout-aliasing defect);
- keyset pagination compiled into opaque cursors; `ids`/`slugs` chunking at
  `max_ids_per_query`;
- `Retry-After` extraction on 429 → `CorpusRateLimited(retry_after=…)`;
- `stream_asset` native (httpx stream); `fetch_asset` inherited from the base
  with its mandatory `max_bytes`;
- 200-with-`data:null` → `DocumentNotFound(source="empty")`; 404 →
  `DocumentNotFound(source="status")` — the distinction memaflow2's parity
  needs;
- capabilities: everything in §4.3's enum except nothing — this backend is the
  full house; `max_ids_per_query=100`, `id_format="uuid"`.

### 5.3 What a hypothetical second adapter looks like

The test of the design. A WordPress-backed customer:

- `WordPressCorpus(Corpus)` over WP REST (`/wp-json/wp/v2/posts`);
- capabilities: `{ARTICLES, ARTICLE_LISTING, ARTICLE_BY_SLUG, SECTIONS,
  DATE_FILTER, ASSETS, CHANGE_SIGNALS}` — **no EDITIONS**;
- memaflow2's requirement check fails fast at boot naming `editions`,
  `edition_pdf`, `result_webhook` — the true integration gap, surfaced on day
  one as a printed list instead of discovered as a NoneType crash in week
  three;
- the contract suite (§7.2) is the definition of done for the adapter.

---

## 6. FastAPI integration

Per-service wiring, shown for a service in memaflow2's shape:

```python
class CorpusSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEMA_CORPUS__")

    backend: Literal["directus", "fake"] = "directus"
    base_url: str
    api_key: SecretStr
    timeout_json: float = 10.0
    timeout_asset: float = 120.0


def build_corpus(settings: CorpusSettings) -> Corpus:
    """Factory — the only place a concrete adapter class is named."""
    match settings.backend:
        case "directus":
            return DirectusCorpus(
                base_url=settings.base_url,
                api_key=settings.api_key,
                timeouts=Timeouts(settings.timeout_json, settings.timeout_asset),
            )
        case "fake":
            return FakeCorpus.from_fixture_dir(...)  # staging/demo/e2e


@asynccontextmanager
async def lifespan(app: FastAPI):
    corpus = build_corpus(get_settings().corpus)
    corpus.require(SERVICE_REQUIREMENTS)  # fail fast, names the gap
    await corpus.ping()  # fail fast, auth/connectivity
    app.state.corpus = corpus
    yield
    await corpus.aclose()


def get_corpus(request: Request) -> Corpus:
    return request.app.state.corpus  # route deps: Depends(get_corpus)
```

One exception handler derives HTTP semantics from the taxonomy — written once,
correct for every adapter:

| Corpus error | HTTP |
|---|---|
| `DocumentNotFound` | 404 |
| `InvalidDocument` | 422 |
| `CorpusAuthError` / `CorpusConfigError` | 502 (server-side config, not the caller's fault) |
| `CorpusUnavailable` | 503 |
| `CorpusRateLimited` | 503 + `Retry-After` |
| `CapabilityNotSupported` | 501 |

`/health` reports `{"status", "corpus": bool}` via `ping()` — same pattern
mema-read already uses for its DB. In Temporal consumers, activities call the
corpus and one `translate_corpus_error()` (§4.5 table) wraps it into
`ApplicationError`; retry policies list non-retryable types exactly as today.

The `backend: "fake"` arm is not a toy: it gives every consumer a
no-network demo/e2e mode for free (pdfmanifesto's benchmark scripts, mema4
demos, CI without secrets).

---

## 7. TDD strategy

### 7.1 Development order (red → green per layer)

1. **`corpus.models` + `corpus.errors`** — table tests for normalisation
   (HTML stripping, date folding, `""` folding) ported from
   `test_models_directus.py`; frozen-ness asserted (mutation raises).
2. **`corpus.query` + `corpus.capabilities`** — requirement-check tests
   (missing capability names appear in the message); query axes ↔ capability
   mapping.
3. **`corpus.testing.fake` against the contract suite** — the fake is the
   first adapter and proves the suite runs before any HTTP exists.
4. **`corpus_directus.compile` + `rows`** — pure-function table tests: every
   §2.3 grammar row asserted (`ArticleQuery(ids=…)` →
   `{"filter[id][_in]": …}`, chunking at 100, projection generated from the
   schema, `sort=-datePublished`, …). These tests are cheap, exhaustive, and
   need no mocks.
5. **`DirectusCorpus` over respx** — the contract suite plus the error-mapping
   table: one respx scenario per row of §2.4's memaflow2 table (200+null,
   404, 401, 5xx, timeout, connect, 429+Retry-After, malformed row,
   oversized asset), each asserting taxonomy member, `retryable`,
   `retry_after`.
6. **`inbound.parse_change`** — cross-product table `{absent body, empty
   body, valid, unknown enum value, malformed date, extra keys}` × asserting
   the result is always a valid `ChangeSignal`, never an exception, and
   always ≤ (never >) the explicit value in conservativeness.
7. **Consumer migration (memaflow2)** — the parity gate: existing 525-test
   suite passes with only the four edit classes already itemized in the
   2026-08-30 plan §10.1; plus a **Temporal `Replayer` test** against an
   exported production history (Article crosses history in both directions);
   plus the webhook payload byte-identity test.

Coverage: 90 %+ for the library (small, pure, fully mockable) against the
80 % house baseline. Unit tests are layers 1–2 and 4, integration is 3 and
5–6, e2e is the consumer's existing workflow tests over `FakeCorpus`.

### 7.2 The contract suite — the adapter specification

```python
class CorpusContractSuite:
    """Subclass per adapter; provide `corpus` (seeded) as a fixture.
    Passing this suite IS the definition of 'is a Corpus'."""

    async def test_get_article_returns_normalised_article(self, corpus): ...
    async def test_get_article_unknown_id_raises_not_found(self, corpus): ...
    async def test_search_pagination_is_exhaustive_and_duplicate_free(self, corpus): ...
    async def test_iter_articles_equals_drained_search(self, corpus): ...
    async def test_unsupported_capability_raises_capability_error(self, corpus): ...
    async def test_fetch_asset_enforces_max_bytes(self, corpus): ...
    async def test_native_errors_never_escape(self, corpus): ...  # no httpx in raised chain
    async def test_change_parse_never_raises(self, corpus): ...
    async def test_capabilities_are_honest(self, corpus): ...  # every declared cap actually works
```

It runs three times in CI: `FakeCorpus` (keeps the fake honest),
`DirectusCorpus` over respx (keeps the adapter honest), and — behind a manual
marker — `DirectusCorpus` against a staging instance (keeps the *schema
constant* honest, catching the field rename before production does).

### 7.3 Boundary enforcement (lint, not convention)

Carried from the 2026-08-30 plan §11, retargeted:

- `temporalio` banned in the library (ruff banned-api);
- `httpx` banned in `corpus/` (adapters only);
- `DirectusCorpus` is `@final` and does not subclass `httpx.AsyncClient`;
- CI grep fails the build if vendor vocabulary (`items/articles`, `filter[`,
  `datePublished`, `articleBody`, `articleKicker`, `editionDate`) appears in
  any consumer's `src/`, `scripts/`, `tools/` after migration.

---

## 8. Migration and adoption

Phasing preserves the 2026-08-30 plan's sequencing and parity gates; the delta
for the abstraction is small (three extra files: `base.py`, `capabilities.py`,
`testing/contract.py`) and is paid once:

| Phase | Content | Gate |
|---|---|---|
| 0 | Repo `Isagog/mema-corpus` (or extend `manifesto-directus` repo with the two-package layout), CI + private-git token plumbing, `corpus.models`/`errors` | image builds, models tested |
| 1 | `capabilities`, `query`, `base`, `FakeCorpus`, contract suite | fake passes suite |
| 2 | `corpus_directus` complete | adapter passes suite + error table |
| 3 | memaflow2 rewire: activities call `DirectusCorpus` through the §4.5 translation table; `ArticleInput` becomes re-export of `corpus.Article`; `assert_processable` → `ArticlePolicy` | **parity gate** (§7.1.7) |
| 4 | listings: delete the six in-repo forks (scripts/tools) | CI vendor-grep = 0 in memaflow2 |
| 5 | notifier + inbound signals wired (corsie-ready before Directus ships anything) | payload byte-identity; signal table tests |
| 6 | pdfmanifesto adopts (highest value: deletes its own client, breaker fossil, and two-channel errors) | its suite green on `Corpus` |
| 7+ | memazeit, memaprocess, remaining consumers, opportunistically | per-repo |

Versioning discipline unchanged from the prior plan §12: tag-pinned git
dependency, library depends on `pydantic` + `httpx` only, schema changes are
minor bumps.

## 9. Risks and open questions

1. **Abstraction tax vs. the concrete plan.** The honest cost of this proposal
   over pure `manifesto-directus` is the capability mechanism and the contract
   suite (~2–3 files, ~1 extra week). The payoff is entirely in the second
   adapter; if the multi-CMS requirement evaporates again, the port still pays
   rent as the seam that made `FakeCorpus`, the error taxonomy, and the
   boot-time requirement check possible.
2. **Temporal history compatibility** (unchanged risk): `Article` field names
   are frozen; `section`/`language` append with defaults; the Replayer test is
   what turns the claim into a fact.
3. **Where does the edition concept end?** For a customer whose CMS has no
   editions but whose product needs daily bundles, an `Edition` can be
   *derived* (all articles of a date). Decide per-adapter whether to synthesize
   (capability declared) or omit (consumer degrades); the port supports both,
   but the choice is product, not code.
4. **The enrichment boundary.** This proposal keeps KG/pgvector/Mongo out of
   the port. If a future consumer wants "article + summary" as one call, that
   is a *composition service* above two ports, not a fatter corpus. Naming
   that service now prevents the port from growing a `get_summary` it cannot
   honour on any other backend.
5. **Repo naming/visibility** — same open question as the prior plan §15.1:
   `mema-corpus` (private, org-wide) vs. splitting core/adapter repos. One
   repo, two packages is the cheapest start; split only when a second adapter
   has a different release cadence.
