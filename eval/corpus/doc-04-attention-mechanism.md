# The Attention Mechanism in Deep Learning

Attention allocates a fixed-size representation by taking a **weighted sum** of inputs, where weights depend on how well each input “matches” a query. The idea entered mainstream sequence modeling with **Bahdanau et al. (2014)** (“Neural Machine Translation by Jointly Learning to Align and Translate,” ICLR 2015) in neural machine translation: the decoder could **soft-search** over source hidden states instead of compressing the whole sentence into one vector. Earlier additive (“concat”) attention used a small feedforward network to score pairs; the Transformer popularized purely dot-product compatibility for speed on accelerators.

## Query, key, and value

Modern formulations package attention as **queries (Q)**, **keys (K)**, and **values (V)**. Compatibility scores compare queries to keys; the resulting weights scale the values. In **scaled dot-product attention**—used in the Transformer (*Attention Is All You Need*, Vaswani et al., 2017)—scores are dot products divided by √*d<sub>k</sub>* to limit magnitude before softmax, improving training stability. For a batch of sequences, attention is often implemented as a single matrix multiply over **QK<sup>T</sup>** followed by softmax and another multiply with **V**, which is why **multi-head** variants (parallel attention with distinct projections) share the same blueprint described in encoder-centric Transformer summaries.

## Self-attention vs cross-attention

**Self-attention** computes Q, K, and V from the *same* sequence so each position attends to all positions in that sequence (overlapping conceptually with the multi-head blocks described in encoder-focused Transformer articles). **Cross-attention** takes queries from one sequence (e.g., decoder states) and keys/values from another (e.g., encoder outputs), enabling conditional generation. Both reuse the same scoring primitive.

## Complexity and FlashAttention

A length-*n* sequence with full pairwise attention incurs **O(n²)** memory and time in the attention map (in the standard dense formulation), which bottlenecks long contexts. Linear and subquadratic attention variants (Linformer, Performer, state-space models) trade exactness or expressivity for asymptotic savings. **FlashAttention** (Dao et al., NeurIPS 2022; FlashAttention-2 in 2023) reduces memory traffic via tiling and recomputation in GPU SRAM, speeding training and inference without changing the mathematical output—an important optimization as models stretch to 8k, 32k, or longer contexts.

Together, Bahdanau-style alignment and Transformer self-attention form a continuous line from recurrent seq2seq to today’s LLMs, with scaled dot-product attention as the shared computational core—overlapping in terminology and equations with standalone treatments of the 2017 architecture.
