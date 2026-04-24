"""从 eval/corpus 生成固定文件名的预构建 wiki/ + raw/，并打包 zip。

输出：
  eval/pack/e2e-wiki-bench/wiki/*.md
  eval/pack/e2e-wiki-bench/raw/e2e-training-costs.xlsx
  eval/pack/e2e-llm-bench.zip

用法：python eval/scripts/build_e2e_wiki_pack.py
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from textwrap import dedent

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORPUS = _REPO_ROOT / "eval" / "corpus"
_OUT_BASE = _REPO_ROOT / "eval" / "pack" / "e2e-wiki-bench"
_WIKI = _OUT_BASE / "wiki"
_RAW = _OUT_BASE / "raw"

_FM = """---
title: "{title}"
type: concept
source: eval-corpus
updated: "2026-04-22"
tags: [e2e, llm-bench]
---

"""

_CORPUS_MAP: list[tuple[str, str, str]] = [
    ("doc-01-transformer-architecture.md", "transformer-architecture.md", "The Transformer Architecture"),
    ("doc-02-large-language-models.md", "large-language-models.md", "Large Language Models: An Overview"),
    ("doc-03-rlhf.md", "rlhf.md", "Reinforcement Learning from Human Feedback (RLHF)"),
    ("doc-04-attention-mechanism.md", "attention-mechanism.md", "The Attention Mechanism in Deep Learning"),
    (
        "doc-05-rag-vs-fine-tuning.md",
        "rag-vs-fine-tuning.md",
        "RAG versus Fine-Tuning for Knowledge-Heavy Applications",
    ),
    (
        "doc-06-knowledge-management-with-llms.md",
        "knowledge-management-with-llms.md",
        "Knowledge Management with LLMs: The Compiled Wiki Pattern",
    ),
]


def _wrap_body(src_path: Path, title: str) -> str:
    body = src_path.read_text(encoding="utf-8").strip() + "\n"
    return _FM.format(title=title) + body


def _write_training_cost_md() -> None:
    md = dedent(
        '''\
        ---
        title: "Training cost reference table"
        type: reference
        source: eval-corpus
        updated: "2026-04-22"
        tags: [e2e, llm-bench, costs]
        ---

        # Training cost reference (illustrative)

        Industry-discussion style estimates used **only for retrieval evaluation**, not as financial advice.

        | Model | Params_B | EstCost_USD |
        | --- | ---: | ---: |
        | GPT-2 | 1.5 | 50000 |
        | PaLM | 540 | 8000000 |
        '''
    )
    (_WIKI / "e2e-training-costs.md").write_text(md, encoding="utf-8")


def _write_product_line_md() -> None:
    md = dedent(
        '''\
        ---
        title: "Fictional edge device product line (eval)"
        type: reference
        source: eval-corpus
        updated: "2026-04-22"
        tags: [e2e, hardware-fixture]
        ---

        # Fictional product line (E2E only)

        ## Product Alpha-900

        Alpha-900 is a **rack-mount inference accelerator** for datacenter drafts.

        - SKU: **ALPHA-900-RACK**
        - Typical power: **420 W**
        - Host interface: **PCIe 5.0 x16**

        ## Product Beta-One

        Beta-One is a **USB-C edge accelerator** for laptops.

        - SKU: **BETA-ONE-USB**
        - Typical power: **8 W**
        - Host interface: **USB4**

        When answering about Alpha-900, **do not substitute Beta-One specifications**.
        '''
    )
    (_WIKI / "e2e-product-line.md").write_text(md, encoding="utf-8")


def _write_overview_and_index() -> None:
    overview = dedent(
        '''\
        ---
        title: "LLM techniques — eval overview"
        type: index
        source: eval-corpus
        updated: "2026-04-22"
        tags: [e2e, overview]
        ---

        # LLM techniques overview (evaluation bundle)

        This overview links the main topics in the **E2E LLM benchmark** wiki pack.

        - [Transformer architecture](transformer-architecture.md)
        - [Attention mechanism](attention-mechanism.md)
        - [Large language models](large-language-models.md)
        - [RLHF](rlhf.md)
        - [RAG vs fine-tuning](rag-vs-fine-tuning.md)
        - [Knowledge management / compiled wiki](knowledge-management-with-llms.md)
        - [Training cost table](e2e-training-costs.md)
        - [Product line fixture](e2e-product-line.md)
        '''
    )
    (_WIKI / "overview.md").write_text(overview, encoding="utf-8")

    index = dedent(
        '''\
        ---
        title: "Index"
        type: index
        source: eval-corpus
        updated: "2026-04-22"
        ---

        # Index

        Start at [overview](overview.md).
        '''
    )
    (_WIKI / "index.md").write_text(index, encoding="utf-8")


def _write_xlsx() -> None:
    try:
        import openpyxl
    except ImportError as e:
        raise SystemExit("需要 openpyxl：pip install openpyxl") from e

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "TrainingCost"
    ws.append(["Model", "Params_B", "EstCost_USD"])
    ws.append(["GPT-2", 1.5, 50000])
    ws.append(["PaLM", 540, 8000000])
    path = _RAW / "e2e-training-costs.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _zip_pack() -> Path:
    zip_path = _REPO_ROOT / "eval" / "pack" / "e2e-llm-bench.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in _WIKI.rglob("*"):
            if f.is_file():
                arc = f.relative_to(_OUT_BASE)
                zf.write(f, arc)
        for f in _RAW.rglob("*"):
            if f.is_file():
                arc = f.relative_to(_OUT_BASE)
                zf.write(f, arc)
    return zip_path


def main() -> None:
    _WIKI.mkdir(parents=True, exist_ok=True)
    _RAW.mkdir(parents=True, exist_ok=True)

    for src_name, dst_name, title in _CORPUS_MAP:
        src = _CORPUS / src_name
        if not src.is_file():
            raise SystemExit(f"missing corpus file: {src}")
        (_WIKI / dst_name).write_text(_wrap_body(src, title), encoding="utf-8")

    _write_training_cost_md()
    _write_product_line_md()
    _write_overview_and_index()
    _write_xlsx()
    zip_path = _zip_pack()
    print(f"Wrote {_OUT_BASE} and {zip_path}")


if __name__ == "__main__":
    main()
