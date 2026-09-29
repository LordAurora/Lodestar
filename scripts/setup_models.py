"""Download everything Lodestar needs to run offline. Run once (`make setup`).

This is the only step that uses the internet. It fetches:

1. tree-sitter grammars for Python, JavaScript, TypeScript, Go, Java and C#;
2. the Foundry Local embedding model (qwen3-embedding-0.6b) and the default
   chat model (qwen2.5-coder-1.5b);
3. optionally (--fastembed) the fastembed ONNX models used as a fallback.

After this, the app runs with outbound network access blocked.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_config  # noqa: E402

GRAMMARS = ["python", "javascript", "typescript", "tsx", "go", "java", "csharp"]


def download_grammars() -> None:
    import tree_sitter_language_pack as tslp

    print("tree-sitter grammars ...", flush=True)
    tslp.download(GRAMMARS)
    print(f"  ok: {', '.join(sorted(tslp.downloaded_languages()))}")


def download_foundry(models: list[str]) -> None:
    from app.foundry import FoundryService

    foundry = FoundryService()
    foundry.start()
    for alias in models:
        model = foundry.manager.catalog.get_model(alias)
        if model is None:
            print(f"  ! {alias} is not in the Foundry Local catalog")
            continue
        if model.is_cached:
            print(f"  ok: {alias} (already downloaded)")
            continue
        last = [-10.0]

        def progress(pct: float, alias=alias, last=last) -> None:
            if pct - last[0] >= 10:
                print(f"  {alias}: {pct:.0f}%", flush=True)
                last[0] = pct

        print(f"Foundry Local: downloading {alias} ...", flush=True)
        model.download(progress)
        print(f"  ok: {alias}")
    foundry.stop()


def download_fastembed() -> None:
    from fastembed import TextEmbedding

    from app.embeddings import FASTEMBED_MODELS

    cache = get_config().models_dir
    for name in FASTEMBED_MODELS:
        print(f"fastembed: {name} ...", flush=True)
        TextEmbedding(name, cache_dir=str(cache))
        print(f"  ok: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download models for offline use.")
    parser.add_argument("--chat-model", default=get_config().default_chat_model)
    parser.add_argument("--fastembed", action="store_true", help="Also fetch fastembed models")
    parser.add_argument("--skip-foundry", action="store_true")
    args = parser.parse_args()

    download_grammars()
    if not args.skip_foundry:
        download_foundry(["qwen3-embedding-0.6b", args.chat_model])
    if args.fastembed:
        download_fastembed()
    print("\nDone. Lodestar can now run fully offline.")


if __name__ == "__main__":
    main()
