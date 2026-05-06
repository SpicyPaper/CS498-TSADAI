import json
import re

from src.local_agent import OllamaAgent

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


class CapabilityClassifier:
    def __init__(
        self,
        model: str = "qwen3:1.7b",
        host: str = "http://localhost:11434",
        timeout_s: float = 60.0,
    ) -> None:
        self.agent = OllamaAgent(
            model=model,
            host=host,
            timeout_s=timeout_s,
            system_prompt=(
                "You classify the user's latest request for routing. "
                "Return only a JSON object whose keys are capability names and values are need scores from 0.0 to 1.0. "
                "Choose 1 to 3 capabilities. Use fewer when the request is simple. "
                "The main capability should usually be between 0.7 and 1.0. Secondary capabilities should usually be between 0.2 and 0.6. "
                "Allowed capabilities: "
                "general for casual chat, simple facts, broad explanations, ambiguous requests; "
                "math for calculations, equations, formulas, proofs, probability, statistics; "
                "programming for writing, modifying, explaining, or debugging code; Python functions; APIs; scripts; algorithms; software implementation; "
                "writing for producing or improving non-code prose text: drafting, rewriting, grammar, translation, emails, essays, reports, documentation wording, style, tone; "
                "summarization for summaries, shortening, extracting key points, condensing text; "
                "research for comparing, investigating, background information, sources, citations, current facts; "
                "planning for plans, schedules, roadmaps, strategies, checklists, steps; "
                "creative for inventing or generating imaginative content: stories, poems, jokes, brainstorming, names, slogans, characters, dialogue, concepts, ideas. "
                "Resolve overlaps by the requested output: code output is programming; prose output is writing; imaginative output is creative; condensed output is summarization; numeric/formal reasoning is math. "
                "If the request includes conversation history, classify mainly the latest user message. "
                "Use general only for broad, casual, or ambiguous requests where no specialized capability clearly fits. "
                "Output JSON only."
            ),
            num_predict=96,
            temperature=0.0,
            think=False,
        )

    async def classify_scores(self, prompt: str) -> dict[str, float]:
        raw = await self.agent.generate(
            f"Latest user request:\n{prompt}\n\nCapability score JSON:"
        )

        scores = self._parse_scores(raw)
        if scores:
            return scores

        capability = self._parse_capability(raw)
        if capability in CAPABILITIES:
            return {capability: 1.0}

        return {"general": 0.6}

    def _parse_scores(self, raw: str) -> dict[str, float]:
        text = raw.strip()
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            text = match.group(0)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}

        if not isinstance(data, dict):
            return {}

        scores: dict[str, float] = {}
        for capability, score in data.items():
            capability = str(capability).strip().lower()
            if capability not in CAPABILITIES:
                continue
            try:
                value = float(score)
            except (TypeError, ValueError):
                continue
            scores[capability] = max(0.0, min(1.0, value))

        specialized_scores = {
            capability: score
            for capability, score in scores.items()
            if capability != "general"
        }
        if specialized_scores and max(specialized_scores.values()) >= 0.5:
            scores.pop("general", None)

        return dict(
            sorted(
                scores.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:3]
        )

    def _parse_capability(self, raw: str) -> str | None:
        lowered = raw.strip().lower()

        if lowered in CAPABILITIES:
            return lowered

        words = lowered.replace(":", " ").replace(",", " ").replace(".", " ").split()
        matches = [capability for capability in CAPABILITIES if capability in words]

        if len(matches) == 1:
            return matches[0]

        return None
