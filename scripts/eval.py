"""Retrieval evaluation: how often does search find the right files?

Reads a YAML file of questions and the file(s) that should answer them,
indexes the sample repository into a throw-away data directory, and reports
two standard metrics for vector-only, BM25-only and hybrid retrieval:

* hit@K - the share of questions where at least one expected file appears
  in the top K results;
* MRR   - mean reciprocal rank: 1 / (rank of the first expected result),
  averaged over questions. 1.0 means the right file is always ranked first.

Usage (from the repository root):

    python scripts/eval.py
    python scripts/eval.py --embedder fastembed:BAAI/bge-small-en-v1.5 --k 5
    python scripts/eval.py --embedder foundry:qwen3-embedding-0.6b
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_config  # noqa: E402
from app.db import Database  # noqa: E402
from app.embeddings import create_embedder  # noqa: E402
from app.foundry import FoundryService  # noqa: E402
from app.indexer import IndexProgress, index_repository  # noqa: E402
from app.retrieval import Retriever  # noqa: E402

MODES = ["vector", "bm25", "hybrid"]


def evaluate(retriever: Retriever, repo_id: str, questions: list[dict], k: int) -> dict:
    results = {}
    for mode in MODES:
        hits, reciprocal_ranks = 0, []
        for item in questions:
            expected = set(item["expected"])
            found = retriever.search(repo_id, item["question"], top_k=k, mode=mode).chunks
            ranks = [i for i, c in enumerate(found, start=1) if c.file_path in expected]
            hits += bool(ranks)
            reciprocal_ranks.append(1 / ranks[0] if ranks else 0.0)
        results[mode] = {
            "hit": hits / len(questions),
            "mrr": sum(reciprocal_ranks) / len(questions),
        }
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default=str(ROOT / "examples" / "bookshelf.eval.yaml"))
    parser.add_argument("--repo", default=None, help="Repository path (default: from dataset)")
    parser.add_argument("--embedder", default=None, help="Same values as LODESTAR_EMBEDDING_MODEL")
    parser.add_argument("--k", type=int, default=6)
    args = parser.parse_args()

    dataset = yaml.safe_load(Path(args.dataset).read_text("utf-8"))
    repo_path = Path(args.repo or Path(args.dataset).parent / dataset["repo"]).resolve()
    questions = dataset["questions"]

    config = get_config()
    embedder_name = args.embedder or config.embedding_model
    foundry = FoundryService(app_name="lodestar")
    embedder = create_embedder(embedder_name, foundry, config.models_dir, offline=config.offline)

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp))
        repo = db.add_repo(str(repo_path), repo_path.name)
        start = time.time()
        progress = index_repository(db, repo["id"], repo_path, embedder, IndexProgress())
        if progress.error:
            raise SystemExit(f"Indexing failed: {progress.error}")
        index_seconds = time.time() - start
        retriever = Retriever(db, embedder)
        results = evaluate(retriever, repo["id"], questions, args.k)

    print(f"\nRepository : {repo_path}")
    print(f"Embedder   : {embedder.name}")
    print(f"Indexed    : {progress.chunks_embedded} chunks in {index_seconds:.1f}s")
    print(f"Questions  : {len(questions)}\n")
    header = f"| {'Retrieval':<10} | {'hit@' + str(args.k):>7} | {'MRR':>6} |"
    print(header)
    print("|" + "-" * 12 + "|" + "-" * 9 + "|" + "-" * 8 + "|")
    for mode in MODES:
        r = results[mode]
        print(f"| {mode:<10} | {r['hit']:>7.2f} | {r['mrr']:>6.3f} |")
    foundry.stop()


if __name__ == "__main__":
    main()
