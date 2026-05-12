from __future__ import annotations

import hashlib
import random

from src.logging_utils import log


CAPABILITIES = [
    "general",
    "programming",
    "writing",
    "summarization",
    "research",
    "planning",
    "creative",
    "math",
]


class CapabilityScorer:
    """
    Simulated capability scoring for local demos.

    Each node receives one strong, one medium, and one weak capability score.
    The values are deterministic for a given node name and primary capability.
    """

    HIGH_RANGE = (0.8, 1.0)
    MEDIUM_RANGE = (0.5, 0.75)
    LOW_RANGE = (0.1, 0.45)

    def __init__(
        self,
        role_hint_capabilities: list[str] | None = None,
        node_name: str | None = None,
    ) -> None:
        self.role_hint_capabilities = self._normalize_capabilities(
            role_hint_capabilities or ["general"]
        )
        self.node_name = node_name or "node"

    async def score_all(self) -> dict[str, float]:
        primary = self.role_hint_capabilities[0]
        rng = random.Random(self._seed(primary))
        remaining = [capability for capability in CAPABILITIES if capability != primary]
        rng.shuffle(remaining)

        medium = remaining[0] if remaining else primary
        low = remaining[1] if len(remaining) > 1 else primary

        scores = {
            primary: self._sample_score(rng, self.HIGH_RANGE),
            medium: self._sample_score(rng, self.MEDIUM_RANGE),
            low: self._sample_score(rng, self.LOW_RANGE),
        }

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        log(
            "SCORES",
            "Generated simulated capability scores "
            f"node={self.node_name!r} primary={primary!r} "
            f"medium={medium!r} low={low!r} scores={scores}",
        )
        log("SCORES", f"Simulated top capabilities: {ranked[:3]}")
        return scores

    async def score_capabilities(self, capabilities: list[str]) -> dict[str, float]:
        scores = await self.score_all()
        selected = self._normalize_capabilities(capabilities)
        return {
            capability: score
            for capability, score in scores.items()
            if capability in selected
        }

    def _normalize_capabilities(self, capabilities: list[str]) -> list[str]:
        selected: list[str] = []
        for capability in capabilities:
            value = str(capability).strip().lower()
            if value in CAPABILITIES:
                selected.append(value)
        return list(dict.fromkeys(selected)) or ["general"]

    def _seed(self, primary: str) -> int:
        raw = f"{self.node_name}:{primary}:{','.join(CAPABILITIES)}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return int(digest[:16], 16)

    def _sample_score(
        self,
        rng: random.Random,
        score_range: tuple[float, float],
    ) -> float:
        lower, upper = score_range
        return round(rng.uniform(lower, upper), 3)
