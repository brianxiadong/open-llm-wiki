# The Transformer Architecture (2017)

The Transformer is a neural network architecture introduced by Vaswani et al. in the paper *Attention Is All You Need* (NeurIPS 2017), written while the authors were at Google Brain and Google Research. It dispenses with recurrence and convolutions for sequence modeling and instead relies entirely on attention mechanisms, a design choice that helped it replace recurrent models such as LSTMs and GRUs as the default backbone for large-scale natural language processing.

## Encoder–decoder structure

The original Transformer follows an encoder–decoder layout suited to sequence-to-sequence tasks such as machine translation. The **encoder** stacks identical layers, each containing multi-head self-attention followed by a position-wise feedforward network, with residual connections and layer normalization. The **decoder** is similar but inserts a masked self-attention block so positions cannot attend to future tokens, and adds **cross-attention** layers where decoder states attend to encoder outputs.

## Self-attention and multi-head attention

**Self-attention** lets every position in a sequence attend to every other position in the same sequence, producing weighted combinations of value vectors. **Multi-head attention** runs several attention operations in parallel (the paper uses *h* = 8 heads with *d<sub>k</sub>* = *d<sub>v</sub>* = 64 per head on the base model), concatenates the results, and projects them linearly. This allows the model to capture different relational patterns simultaneously.

## Positional encoding and feedforward layers

Because attention is permutation-invariant without extra structure, **positional encodings** are added to input embeddings—sinusoidal functions of position in the original work—so order is represented explicitly. Each sublayer is followed by a **position-wise feedforward network**: two linear transformations with a ReLU activation in between, applied identically at each position (dimensionality 512 → 2048 → 512 in the base model).

## Impact

With roughly 65 million parameters in the base configuration and strong parallelization during training, the 2017 model set new benchmarks on WMT translation tasks. The architecture became the foundation for later systems from BERT to GPT-style models and remains central to modern large language model design.
