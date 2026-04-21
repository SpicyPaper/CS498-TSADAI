import trio
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from abc import ABC, abstractmethod


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
        max_new_tokens: int = 128,
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
