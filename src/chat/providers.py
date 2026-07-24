from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


class ProviderError(RuntimeError):
    pass


def _line_has_valid_citation(line: str, allowed_ids: set[str]) -> bool:
    if not re.search(r"[A-Za-z0-9]", line):
        return True
    cited = set(re.findall(r"\[([A-Za-z0-9-]+)\]", line))
    return bool(cited) and cited.issubset(allowed_ids)


def generate_with_configured_llm(
    question: str,
    evidence: list[dict],
    fallback: str,
    session_id: str | None = None,
) -> dict | None:
    """Call an OpenAI-compatible chat endpoint when explicitly configured.

    The response must retain at least one allowed [citation-id]. Invalid or
    uncited model output is rejected so the caller can use the grounded fallback.
    """

    provider = os.getenv("ANSWER_PROVIDER", "local-extractive-v2")
    if provider not in {"openai-compatible", "fireworks"}:
        return None
    if provider == "fireworks":
        base_url = "https://api.fireworks.ai/inference/v1"
        api_key = os.getenv("FIREWORKS_API_KEY", "")
        model = os.getenv("FIREWORKS_MODEL", "accounts/fireworks/models/gpt-oss-20b")
    else:
        base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
        api_key = os.getenv("LLM_API_KEY", "")
        model = os.getenv("LLM_MODEL", "")
    if not base_url or not api_key or not model:
        raise ProviderError(f"ANSWER_PROVIDER is {provider} but its API URL, key, or model is missing")

    allowed_ids = {item["id"] for item in evidence}
    context = "\n".join(f"[{item['id']}] {item['source']} page {item.get('page')}: {item['text']}" for item in evidence)
    system = (
        "You answer questions about two engineering document revisions. Use only the supplied evidence. "
        "Return concise plain text with one factual claim per line and no heading. "
        "Every non-empty line must end with one or more supplied citation IDs in square brackets. "
        "Use citation IDs exactly as supplied. If the evidence is insufficient, say so and cite the evidence that "
        "shows the limitation. Never invent a tag, value, page, or change."
    )
    user = f"Question: {question}\n\nEvidence:\n{context}\n\nGrounded draft:\n{fallback}"
    payload = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode("utf-8")
    if provider == "fireworks" and "gpt-oss" in model:
        request_body = json.loads(payload)
        request_body["reasoning_effort"] = "low"
        payload = json.dumps(request_body).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if session_id:
        headers["x-session-affinity"] = session_id
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"{provider} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"{provider} could not be reached") from exc
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderError(f"{provider} returned an invalid response") from exc
    answer = result["choices"][0]["message"]["content"].strip()
    cited = set(re.findall(r"\[([A-Za-z0-9-]+)\]", answer))
    factual_lines = [line.strip() for line in answer.splitlines() if line.strip()]
    if not cited or not cited.issubset(allowed_ids) or not all(
        _line_has_valid_citation(line, allowed_ids) for line in factual_lines
    ):
        return None
    usage = result.get("usage", {})
    input_tokens = int(usage.get("prompt_tokens", 0))
    output_tokens = int(usage.get("completion_tokens", 0))
    if provider == "fireworks" and model.endswith("gpt-oss-20b"):
        input_rate = float(os.getenv("FIREWORKS_INPUT_COST_PER_MILLION", "0.07"))
        output_rate = float(os.getenv("FIREWORKS_OUTPUT_COST_PER_MILLION", "0.30"))
    else:
        input_rate = float(os.getenv("LLM_INPUT_COST_PER_MILLION", "0"))
        output_rate = float(os.getenv("LLM_OUTPUT_COST_PER_MILLION", "0"))
    cost = input_tokens / 1_000_000 * input_rate + output_tokens / 1_000_000 * output_rate
    return {
        "answer": answer,
        "provider": f"{provider}:{model}",
        "prompt": f"SYSTEM:\n{system}\n\nUSER:\n{user}",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": round(cost, 8),
        "cited_ids": sorted(cited),
        "response_id": result.get("id"),
        "finish_reason": result.get("choices", [{}])[0].get("finish_reason"),
    }
