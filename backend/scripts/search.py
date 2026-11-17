"""Search the knowledge base as a given person and print what retrieval returns.

Usage (from the backend folder, after ingesting):
    python scripts/search.py --as nex_arjun "When is the Project Atlas launch?"
    python scripts/search.py --as nex_dev "What is the salary band for backend engineers?"
    python scripts/search.py --as vtx_sana --org org_vertex "When does Project Atlas go live?"

Results are what this person is allowed to see, ranked by vector, keyword and graph search.
"""

import argparse
import sys

from app.config import get_settings
from app.modules.identity import AccessDenied, AccessService
from app.modules.identity.ids import organisation_id
from app.modules.retrieval import RetrievalResult, RetrievalService, RetrievalSettings
from app.shared.database import UnitOfWork, create_database_engine
from app.shared.embeddings.sentence_transformer_provider import SentenceTransformerEmbeddings


def main() -> int:
    args = parse_args()
    settings = get_settings()
    if settings.database_url is None:
        print("DATABASE_URL is not set (add it to the .env file).", file=sys.stderr)
        return 1
    engine = create_database_engine(settings.database_url.get_secret_value())
    unit_of_work = lambda: UnitOfWork(engine)  # noqa: E731

    try:
        org = organisation_id(args.org) if args.org else None
        access = AccessService(unit_of_work).for_person(args.person, org)
    except AccessDenied as error:
        print(f"Access denied: {error}", file=sys.stderr)
        return 1

    embedder = SentenceTransformerEmbeddings(
        settings.embedding_model,
        expected_dimension=settings.embedding_dimension,
        device=settings.embedding_device,
    )
    retrieval = RetrievalService(unit_of_work, embedder, RetrievalSettings(max_results=args.limit))
    print(
        f"Searching as {access.full_name} ({access.role.value}, "
        f"{len(access.access_group_ids)} access groups)\n"
    )
    print_result(retrieval.search(access, args.question))
    return 0


def print_result(result: RetrievalResult) -> None:
    if result.matched_entities:
        print("Entities in the question: " + ", ".join(result.matched_entities))
    if result.is_empty:
        print("No visible sources match this question.")
        return
    for position, hit in enumerate(result.hits, start=1):
        ranks = " ".join(f"{channel.value}#{rank}" for channel, rank in sorted(hit.ranks.items()))
        similarity = f" sim={hit.similarity:.2f}" if hit.similarity is not None else ""
        print(
            f"[{position}] {hit.title}  ({hit.external_id} v{hit.source_version}, "
            f"{hit.source_type}, {hit.occurred_at:%d %b %Y})"
        )
        print(f"    score={hit.score:.4f}{similarity}  {ranks}")
        print("    " + " ".join(hit.content.split())[:220])
    if result.graph_facts:
        print("\nGraph facts:")
        for fact in result.graph_facts:
            print(f"  {fact.subject} --{fact.relationship_type}--> {fact.object}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--as", dest="person", required=True, help="directory id, e.g. nex_arjun")
    parser.add_argument("--org", help="organisation id, needed if the person is in several")
    parser.add_argument("--limit", type=int, default=8)
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
