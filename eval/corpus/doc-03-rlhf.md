# Reinforcement Learning from Human Feedback (RLHF)

**Reinforcement learning from human feedback (RLHF)** trains language models to better match **human preferences**—helpfulness, harmlessness, and stylistic norms—beyond raw likelihood on web text. The motivation is **alignment**: base models optimize perplexity, not user intent, so additional training shapes outputs toward what people actually want. In practice, labeler guidelines, rubrics, and demographic mix strongly influence what “preference” means.

## Three-step pipeline

1. **Supervised fine-tuning (SFT):** Collect demonstrations of desired behavior and fine-tune the pretrained model on these prompt–response pairs (InstructGPT, 2022, used roughly 13k such examples).

2. **Reward model (RM) training:** Human labelers rank several model outputs for the same prompts. A separate model is trained to predict those rankings, producing a scalar **reward** for policy optimization. The RM is typically initialized from the SFT checkpoint plus an added value head.

3. **RL optimization:** The language model is treated as a policy optimized with **proximal policy optimization (PPO)** to maximize reward while staying close to the SFT policy. A **KL divergence penalty** against the reference (often the SFT model) discourages collapse into reward hacking and preserves fluency; implementations tune a coefficient *β* (often on the order of **0.01–0.2** in published setups, varying by stack) to balance improvement versus drift.

## InstructGPT and ChatGPT

OpenAI’s **InstructGPT** (March 2022, described in *Training language models to follow instructions with human feedback*) validated RLHF at moderate scale; **ChatGPT** (November 2022) extended the recipe with conversational data. Both showed that modest human data plus RL could yield large perceived gains over the base GPT-3.5-class model. Limitations remain: reward models can be **brittle** under distribution shift, and PPO runs add engineering overhead (rollout generation, advantage estimation, multiple models in memory).

## Alternatives

**Direct preference optimization (DPO)** (Rafailov et al., 2023) and related methods bypass explicit reward modeling by optimizing directly on preference pairs, often with greater stability. **Constitutional AI** (Anthropic, Bai et al., 2022) uses principles and AI feedback to reduce reliance on human labels for harmlessness. **RLAIF** substitutes model-generated preferences for some human labels. These approaches address similar goals with different trade-offs in data efficiency and implementation complexity.
