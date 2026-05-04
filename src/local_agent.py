import trio
import json
import urllib.error
import urllib.request

from transformers import AutoModelForCausalLM, AutoTokenizer

from abc import ABC, abstractmethod

from src.ollama_utils import OllamaError, ollama_generate_url


class LocalAgent(ABC):
    @abstractmethod
    async def generate(self, prompt: str) -> str:
        """
        Generate an answer locally.
        """
        raise NotImplementedError


class DummyAgent(LocalAgent):
    async def generate(self, prompt: str) -> str:
        """
        Temporary placeholder for a real LLM.
        """
        return f"[LOCAL DUMMY ANSWER] I received: {prompt}"


class QwenAgent(LocalAgent):
    def __init__(
        self,
        model_id: str = "Qwen/Qwen3-0.6B",
        max_new_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.8,
        top_k: int = 20,
        enable_thinking: bool = False,
    ) -> None:
        self.model_id = model_id
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.enable_thinking = enable_thinking

        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id,
            torch_dtype="auto",
            device_map="auto",
        )

    async def generate(self, prompt: str) -> str:
        return await trio.to_thread.run_sync(self._generate_sync, prompt)

    def _generate_sync(self, prompt: str) -> str:
        messages = [
            {"role": "user", "content": prompt},
        ]

        text = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=self.enable_thinking,
        )

        inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)

        outputs = self.model.generate(
            **inputs,
            max_new_tokens=self.max_new_tokens,
            do_sample=True,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
        )

        generated_ids = outputs[0][len(inputs.input_ids[0]) :]
        answer = self.tokenizer.decode(
            generated_ids,
            skip_special_tokens=True,
        )

        return answer.strip()


class OllamaAgent(LocalAgent):
    def __init__(
        self,
        model: str = "qwen3:1.7b",
        host: str = "http://localhost:11434",
        system_prompt: str | None = None,
        timeout_s: float = 300.0,
        num_predict: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.8,
        think: bool = False,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.url = ollama_generate_url(host)
        self.system_prompt = system_prompt
        self.timeout_s = timeout_s
        self.num_predict = num_predict
        self.temperature = temperature
        self.top_p = top_p
        self.think = think

    async def generate(self, prompt: str) -> str:
        return await trio.to_thread.run_sync(self._generate_sync, prompt)

    def _generate_sync(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "think": self.think,
            "options": {
                "num_predict": self.num_predict,
                "temperature": self.temperature,
                "top_p": self.top_p,
            },
        }

        if self.system_prompt is not None:
            payload["system"] = self.system_prompt

        data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            self.url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            detail = raw.strip() or str(exc)
            raise OllamaError(
                f"Ollama request failed for model {self.model!r} at {self.url}: "
                f"HTTP {exc.code}. {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise OllamaError(
                f"Failed to reach Ollama at {self.host}. "
                "Start Ollama with `ollama serve` or open the Ollama desktop app. "
                f"Details: {reason}"
            ) from exc

        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise OllamaError(
                f"Ollama returned invalid JSON for model {self.model!r}."
            ) from exc

        if "error" in result:
            error = str(result["error"])
            hint = ""
            if "not found" in error.lower() or "pull" in error.lower():
                hint = f" Run `ollama pull {self.model}`."
            raise OllamaError(f"Ollama error for model {self.model!r}: {error}.{hint}")

        answer = result.get("response", "")
        return answer.strip()
