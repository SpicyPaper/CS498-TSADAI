"""
Local agent abstraction.

This isolates local query execution from networking logic.

NOTE: Simple dummy implementation.
"""

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
