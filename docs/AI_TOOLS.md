# AI tools used

The brief asks for a disclosure of the tools used. This covers both how the prototype was
built and what it runs on.

> Review this page before submitting and adjust it so it matches exactly how you worked.

## Tools used to build it

| Tool | Used for |
|---|---|
| ChatGPT | Design discussions before coding: requirements, architecture, data model, retrieval and access-control approach, edge cases. The high-level design diagrams in `docs/diagrams/` came out of this phase. |
| Claude (Anthropic) | Writing the code module by module from that design, under my direction and review: synthetic seed data, ingestion, retrieval, answering, tasks, API, UI, tests and these docs. Each module was run and checked against real Supabase and Groq before the next one started, and fixes came from the observed output (for example, the citation and conflict rules in the answer prompt). |

Libraries: FastAPI, Uvicorn, Pydantic, pydantic-settings, SQLAlchemy, psycopg 3, Alembic,
pgvector, sentence-transformers (PyTorch), httpx, PyJWT, pytest, Ruff; supabase-js and
Google Fonts in the browser.

Decisions I made and enforced throughout: clean modular code that follows SOLID and
low-level design principles; Supabase as the single store; open-weight Hugging Face
embeddings; a free Groq-hosted model; building module by module; and testing against
ground-truth questions.

## AI inside the product (runtime)

| Component | Model | Role | Guardrails |
|---|---|---|---|
| Embeddings | `BAAI/bge-base-en-v1.5` (open weights, run locally) | Semantic search over chunks | Normalised 768-d vectors; dimension checked against the database column |
| Answers | Groq `openai/gpt-oss-120b` | Writes the cited answer from retrieved chunks | Only sees chunks the asker may see; strict JSON schema; every citation validated by code, with one corrective retry and a safe failure |
| Graph extraction | same | Proposes entities and relationships at ingestion | Closed vocabulary; names resolved against the directory; disallowed or low-confidence relations rejected by code |
| Task proposals | same | Drafts tasks from agreed action items | Role checked first; assignees, dates and sources resolved by code; nothing created without confirmation |
| Synthetic data (optional) | same | Generates extra dataset variations (`generate_dataset.py`) | Topics that would break the ground truth are filtered; output validated by the dataset rules |

What is never delegated to a model: who may see what, whether a message is a task request,
whether an action is allowed, and whether a citation is real.

## Data note

All companies, people and content in `datasets/` are fictional. Chunks sent to Groq contain
only this synthetic data.
