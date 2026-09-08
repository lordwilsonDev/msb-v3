# Workload A — Research Findings (Baseline)
## Question: What is the current state of sub-7B open-source LLMs for edge deployment on Apple Silicon?

### Claim 1: Sub-7B open-source LLMs are production-ready on Apple Silicon
- Evidence: Qwen3.5 1.5B/3B, Llama 3.2 1B/3B, Phi-3.5-mini 4B, Gemma 2 2B all have quantized variants available.
- Sources:
  - https://huggingface.co/Qwen/Qwen3.5-1.5B-Instruct
  - https://huggingface.co/google/gemma-2-2b-it
- Confidence: high

### Claim 2: MLX is the dominant optimization stack for Apple Silicon edge inference
- Evidence: MLX provides Metal-backed unified memory inference with 4-bit quantization support across most sub-7B models.
- Sources:
  - https://github.com/ml-explore/mlx
  - https://huggingface.co/mlx-community
- Confidence: high

### Claim 3: Quantized sub-7B models fit within Apple Silicon unified memory budgets
- Evidence: Q4_K_M GGUF or MLX 4-bit quantizations fit within 2-4 GB RAM for models up to 4B parameters.
- Sources:
  - https://huggingface.co/mlx-community/Qwen3.5-1.5B-Instruct-4bit
  - https://huggingface.co/mlx-community/Phi-3.5-mini-instruct-4bit
- Confidence: high

### Claim 4: Quantization formats have converged
- Evidence: NVFP4, AWQ, GPTQ, and GGUF are all supported; MLX achieves 20–87% speedup vs llama.cpp under 14B parameters; llama.cpp Metal backend remains CPU-first.
- Sources:
  - https://arxiv.org/html/2607.00501v1
  - https://v-chandra.github.io/on-device-llms
- Confidence: medium

## Memory Append
- No memory append in baseline condition.
