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
                "You are a query router. "
                "Score the useful capabilities for the next answer. "
                "Allowed capabilities: general, math, programming, writing, summarization, research, planning, creative. "
                "general = casual conversation, simple facts, broad explanations, everyday advice, ambiguous queries. "
                "math = arithmetic, equations, calculations, formulas, proofs, probability, statistics. "
                "programming = code, Python, functions, APIs, scripts, algorithms, debugging, software. "
                "writing = rewrite, grammar, translation, email, essay, report, documentation prose. "
                "summarization = summarize, shorten, extract key points, notes, condense provided text. "
                "research = compare, investigate, explain a topic, background, sources, citations, current facts. "
                "planning = plan, schedule, roadmap, strategy, checklist, steps, study plan. "
                "creative = story, poem, joke, brainstorm, names, slogans, characters, dialogue. "
                "If the query contains a section named 'Current user message:', choose the capability mainly for that current message. "
                "Use recent context only to resolve follow-ups, references, or ambiguity. "
                "Do not choose a capability based only on older context if the current user message asks for something different. "
                "Important priority: if the query asks to write code, a Python function, a script, an API, an algorithm, or mentions a programming language, choose programming, not writing. "
                "Return only JSON like {\"programming\":0.9,\"math\":0.4}. "
                "Use scores from 0.0 to 1.0. Include at most three capabilities. "
                "If uncertain, return {\"general\":0.6}. "
                "Do not explain."
            ),
            num_predict=96,
            temperature=0.0,
            think=False,
        )

    async def classify_scores(self, prompt: str) -> dict[str, float]:
        raw = await self.agent.generate(f"Query: {prompt}\nCapability:")

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
