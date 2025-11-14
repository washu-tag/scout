# vLLM Ansible Role

Deploys [vLLM Production Stack](https://github.com/vllm-project/production-stack) for serving large language models in Scout's Chat service.

## Overview

vLLM is a high-performance LLM inference engine that provides:
- **Better concurrency**: 3-19x higher throughput than Ollama via PagedAttention and continuous batching
- **OpenAI-compatible API**: Drop-in replacement for OpenAI endpoints
- **Production features**: Router with session affinity, horizontal scaling, Prometheus metrics
- **Function calling**: Native tool use support for Scout's Trino MCP integration

## Configuration

### Model Selection

Set `vllm_model_name` in `inventory.yaml`:

```yaml
vllm_model_name: gpt-oss-120b  # Default
# or
vllm_model_name: qwen-qwq-32b  # Alternative thinking model
```

#### Available Models

| Model | Parameters | Context | Concurrent Users (A100 80GB) | Tool Calling |
|-------|-----------|---------|------------------------------|--------------|
| **gpt-oss-120b** | 120B (5.1B active) | 128K | 1-2 | OpenAI-style |
| **qwen-qwq-32b** | 32B | 32K | 4 | Hermes-style |

### Backend Selection

Configure in `inventory.yaml`:

```yaml
enable_chat: true
llm_backend: vllm  # Default; use 'ollama' for Ollama backend
```

### GPU Requirements

Set GPU memory requirement in `inventory.yaml`:

```yaml
llm_min_gpu_memory_gb: 80  # Default for GPT-OSS-120B
# llm_min_gpu_memory_gb: 60  # For smaller models
# llm_min_gpu_memory_gb: 0   # Disable GPU memory requirement
```

### Resource Allocation

Override defaults in `inventory.yaml`:

```yaml
vllm_cpu_request: 8
vllm_memory_request: 16Gi
vllm_cpu_limit: 16
vllm_memory_limit: 32Gi
```

### Storage Configuration

```yaml
vllm_storage_size: 100Gi  # Model weights cache
vllm_storage_class: ''    # Uses cluster default; override if needed
```

## Deployment

### Deploy Chat Service

```bash
cd ansible
make install-chat
```

This deploys:
1. vLLM Production Stack (if `llm_backend: vllm`)
2. Open WebUI with backend configured

### Verify Deployment

```bash
# Check vLLM pods
kubectl get pods -n chatbot -l app=vllm-backend

# Check logs (model download takes 10-20 minutes for GPT-OSS-120B)
kubectl logs -n chatbot -l app=vllm-backend -f

# Test endpoint
kubectl exec -n chatbot deployment/vllm-router -- \
  curl -s http://localhost:8000/v1/models
```

## Monitoring

vLLM metrics are automatically scraped by Prometheus when deployed.

**Endpoint**: `http://vllm-router.chatbot:8000/metrics`

**Key Metrics**:
- `vllm_num_requests_waiting` - Queue depth
- `vllm_time_to_first_token_seconds` - TTFT latency
- `vllm_gpu_cache_usage_perc` - KV cache utilization
- `vllm_num_requests_running` - Active requests

Access via Grafana to visualize vLLM performance.

## Architecture

```
Open WebUI → vLLM Router → vLLM Backend Pods
                              └─ GPT-OSS-120B (A100 GPU)
                              └─ Prometheus Metrics
```

### Components

- **vLLM Backend**: Inference engine serving the model
- **vLLM Router**: Load balancer with session affinity for KV cache reuse
- **Storage**: PVC for model weights (auto-downloaded from HuggingFace)

## Troubleshooting

### Pod Not Starting

Model download can take 10-20 minutes for large models:

```bash
kubectl logs -n chatbot -l app=vllm-backend -f
```

Look for download progress. If stuck, check:
- Internet connectivity from cluster
- Sufficient storage (100Gi+ required for GPT-OSS-120B)

### Out of Memory (OOM)

If pods are OOMKilled:

1. **Reduce context length**:
   ```yaml
   # In defaults, or override in inventory
   vllm_model_configs:
     gpt-oss-120b:
       max_model_len: 65536  # Reduce from 131072
   ```

2. **Reduce concurrent users**:
   ```yaml
   vllm_model_configs:
     gpt-oss-120b:
       max_num_seqs: 1  # Reduce from 2
   ```

3. **Check GPU memory**:
   ```bash
   kubectl exec -n chatbot -it deployment/vllm-backend -- nvidia-smi
   ```

### Function Calling Not Working

Verify tool call parser is correct for your model:

```bash
kubectl logs -n chatbot -l app=vllm-backend | grep "tool"
```

GPT-OSS uses `openai` parser, QwQ uses `hermes`.

### Switching Models

To switch from GPT-OSS to QwQ:

```yaml
# inventory.yaml
vllm_model_name: qwen-qwq-32b
```

```bash
make install-chat
```

The new model will be downloaded automatically (may take 10-15 minutes).

## Performance Tuning

### For Higher Concurrency

Use a smaller model or reduce context:

```yaml
vllm_model_name: qwen-qwq-32b  # 32B model, 4 concurrent users
```

### For Maximum Context

Reduce concurrent users:

```yaml
vllm_model_configs:
  gpt-oss-120b:
    max_num_seqs: 1  # Single user, full 128K context
```

### Memory Optimization

vLLM uses FP8 KV cache by default (50% memory savings). To disable:

```yaml
vllm_kv_cache_dtype: auto  # Uses model's native dtype
```

## Scaling

### Adding Second GPU

To increase capacity, configure tensor parallelism:

```yaml
vllm_gpu_count: 2
vllm_tensor_parallel_size: 2
```

This enables:
- GPT-OSS-120B: 4-8 concurrent users at 32K context
- QwQ-32B: 8-12 concurrent users at 32K context

## References

- [vLLM Production Stack](https://github.com/vllm-project/production-stack)
- [vLLM Documentation](https://docs.vllm.ai/)
- [GPT-OSS Model](https://huggingface.co/openai/gpt-oss-120b)
- [Qwen QwQ Model](https://huggingface.co/Qwen/QwQ-32B)
