# Qwen 2.5 Features

## Overview
Qwen 2.5 is a series of code-specific models designed to improve code generation, reasoning, and fixing capabilities. The latest version, qwen2.5-coder, comes in multiple sizes: 0.5B, 1.5B, 3B, 7B, 14B, and 32B.

## Key Features

### Code Generation
Qwen2.5 Coder models have achieved state-of-the-art performance on popular code generation benchmarks such as EvalPlus, LiveCodeBench, and BigCodeBench. The 32B model is competitive with OpenAI's GPT-4o in terms of code generation capabilities.

### Code Repair
The models excel at fixing errors in code, making programming more efficient. Qwen2.5 Coder 32B Instruct scored 73.7 on the Aider benchmark, performing comparably to GPT-4o.

### Code Reasoning
Qwen2.5 Coder models are designed to learn and predict the process of code execution accurately. The recently released qwen2.5 Coder 7B Instruct has shown impressive performance in code reasoning, with the 32B model taking it a step further.

### Multi-Language Support
The models perform well across more than 40 programming languages, scoring 65.9 on McEval and demonstrating strong performance in languages like Haskell and Racket. The multi-language code repair capabilities are also impressive, aiding users in understanding and modifying familiar programming languages.

### Human Preference Alignment
Qwen2.5 Coder models have been evaluated for their alignment with human preferences using an internal benchmark called Code Arena. They perform well in preference alignment tasks compared to other models.

## Model Sizes
Qwen 2.5 is available in multiple sizes to fit different device requirements:
- **0.5B**: Smallest model, suitable for resource-constrained environments.
- **1.5B**: Larger than 0.5B but still relatively small.
- **3B**: A significant improvement over smaller models with better performance.
- **7B**: Further enhances performance with a larger context window.
- **14B**: Offers even more advanced capabilities and a larger context window.
- **32B**: The largest model, providing the most comprehensive features and performance.

## Usage
Qwen 2.5 can be used through various interfaces:
- **CLI**: Command-line interface for running models locally.
- **cURL**: For making HTTP requests to the API.
- **Python**: Using the ollama Python library.
- **JavaScript**: Through the ollama JavaScript library.

## Documentation
For detailed documentation on how to use qwen2.5, please refer to:
- [ollama-python](https://github.com/ollama/ollama-python)
- [ollama-js](https://github.com/ollama/ollama-js)

## References
- [Blog Post](https://qwenlm.github.io/blog/qwen2.5-coder-family/)
- [HuggingFace](https://huggingface.co/collections/Qwen/qwen25-coder-66eaa22e6f99801bf65b0c2f)
