import json
import urllib.error
import urllib.request


class OllamaError(RuntimeError):
    pass


def ollama_generate_url(host: str) -> str:
    return f"{host.rstrip('/')}/api/generate"


def ollama_tags_url(host: str) -> str:
    return f"{host.rstrip('/')}/api/tags"


def check_ollama_ready(
    host: str,
    model: str,
    timeout_s: float = 5.0,
) -> None:
    """
    Fail early with a clear message if Ollama cannot serve the requested model.
    """
    url = ollama_tags_url(host)

    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise OllamaError(
            f"Ollama API is not reachable at {host.rstrip('/')}. "
            "Start Ollama with `ollama serve` or open the Ollama desktop app. "
            f"Details: {reason}"
        ) from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaError(
            f"Ollama API at {host.rstrip('/')} returned invalid JSON from /api/tags."
        ) from exc

    models = payload.get("models", [])
    model_names = {
        item.get("name")
        for item in models
        if isinstance(item, dict) and item.get("name")
    }

    if model not in model_names:
        available = ", ".join(sorted(model_names)) or "<none>"
        raise OllamaError(
            f"Ollama is running at {host.rstrip('/')}, but model {model!r} "
            f"is not installed. Run `ollama pull {model}`. "
            f"Available models: {available}"
        )
