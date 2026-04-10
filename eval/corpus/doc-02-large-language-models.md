# Large Language Models: An Overview

A **large language model (LLM)** is a neural network—typically a Transformer-based architecture—trained on vast text corpora to predict tokens (words or subwords) conditioned on context. “Large” usually refers to scale on the order of **billions of parameters** (e.g., GPT-3 at 175 billion parameters, announced 2020), though the threshold has shifted upward as training and hardware have improved. Token vocabularies often range from tens of thousands to 100k+ subword units (SentencePiece, BPE), and context windows have grown from 2,048 tokens in early GPT checkpoints to 128k or more in frontier 2024–2025 systems.

## Representative models

Early milestones include **GPT** (2018) and **GPT-2** (2019) from OpenAI, **BERT** (2018, Google), which uses bidirectional encoder representations for understanding tasks, and **T5** (2020), which frames tasks as text-to-text. Google’s **PaLM** (2022) reached **540 billion** parameters on the Pathways system; **Chinchilla** (2022) showed that for a fixed compute budget, smaller models trained on **more data** often outperform naïvely larger ones. Open-weight families such as **LLaMA** (Meta, 2023) and commercial or open models like **DeepSeek** (2023 onward) illustrate continued scaling across organizations. Architectures split broadly into **decoder-only** models (GPT-style, suited to generation), **encoder-only** models (BERT-style, strong for classification and embeddings), and encoder–decoder designs.

## Training paradigm

Training typically combines **unsupervised pretraining** on large corpora (next-token prediction or denoising objectives) with **supervised fine-tuning** on task-specific or instruction data. Instruction tuning and **RLHF** (see dedicated surveys) further align behavior with user intent. **Emergent abilities**—behaviors not explicitly trained, such as chain-of-thought-style reasoning—often appear only above certain model and dataset scales, though debate continues on how “emergent” these effects are versus measurement artifacts.

## Compute and trends

Training frontier models has driven **steep growth in compute**: reported budgets often reach thousands of GPU or TPU chip-years for the largest systems, with energy and cost scaling alongside parameter counts. Estimates in industry discussions for top-tier pretraining runs have spanned **tens of millions of dollars** per model generation, depending on hardware utilization and data sourcing. Efficient fine-tuning (LoRA, adapters) and distillation partially offset these costs for downstream deployment, but pretraining at scale remains the dominant expense for new foundation models.
