import json
import re

from src.local_agent import LocalAgent, OllamaAgent
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

CLASSIFIER_SYSTEM_PROMPT = (
    "Classify the user's latest request for routing. "
    "Return only one JSON object. Keys must be capability names. Values must be numbers from 0.0 to 1.0. "
    "Choose 1 to 3 capabilities. Use one capability for simple requests. "
    "Scores measure how much the request needs each capability. "
    "Use high scores for essential capabilities, medium scores for helpful secondary capabilities, and low scores for minor supporting capabilities. "
    "Use values between 0.0 and 1.0 as a continuous scale, not only 0.0 or 1.0. "
    "Use the requested task and expected output as the main signal. "
    "Capabilities: "
    "general: casual chat, simple factual questions, common knowledge, definitions, broad explanations, simple Q&A. "
    "programming: code, code explanation, functions, APIs, scripts, algorithms, debugging, software implementation. "
    "math: arithmetic, equations, formulas, proofs, statistics, quantitative reasoning. "
    "writing: non-code prose drafting, rewriting, grammar, tone, translation, emails, essays, documentation wording. "
    "summarization: summarize, shorten, extract key points, condense text. "
    "research: requests needing external lookup, current facts, citations, evidence, source checking, detailed comparison. "
    "planning: plans, schedules, roadmaps, strategies, checklists, steps. "
    "creative: stories, poems, jokes, names, slogans, brainstorming, fictional characters, imaginative ideas. "
    "Simple known facts, definitions, short explanations, and brief comparisons of well-known concepts are general. "
    "External sources, current information, evidence, and detailed investigation are research. "
    "Conversation history is context; the latest user request is the main request to classify. "
    "Use secondary capabilities only when they are clearly needed by the expected output. "
    "Return the JSON object without any explanation."
)


class CapabilityClassifier:
    def __init__(
        self,
        model: str = "qwen3:1.7b",
        host: str = "http://localhost:11434",
        timeout_s: float = 60.0,
        agent: LocalAgent | None = None,
    ) -> None:
        self.agent = agent or OllamaAgent(
            model=model,
            host=host,
            timeout_s=timeout_s,
            system_prompt=CLASSIFIER_SYSTEM_PROMPT,
            num_predict=96,
            temperature=0.0,
            think=False,
        )

    async def classify_scores(self, prompt: str) -> dict[str, float]:
        raw = await self.agent.generate(
            "Return only JSON for the latest user request.\n"
            f"Allowed keys: {', '.join(CAPABILITIES)}.\n"
            "Values are continuous need scores from 0.0 to 1.0.\n"
            "Use decimals to express partial need.\n\n"
            f"Latest user request:\n{prompt}\n\n"
            "Capability score JSON:"
        )

        scores = self._parse_scores(raw)
        if scores:
            log("ROUTING", f"Classifier raw output={raw!r} parsed={scores}")
            return scores

        capability = self._parse_capability(raw)
        if capability in CAPABILITIES:
            scores = {capability: 1.0}
            log("ROUTING", f"Classifier raw output={raw!r} parsed={scores}")
            return scores

        log("ROUTING", f"Classifier returned invalid output raw={raw!r}")
        raise RuntimeError(
            "Capability classifier returned invalid output. Expected JSON like "
            '{"math": 0.9}. '
            f"Raw output: {raw[:300]!r}"
        )

    def _parse_scores(self, raw: str) -> dict[str, float]:
        text = raw.strip()
        json_candidates = [
            match.group(0)
            for match in re.finditer(r"\{[^{}]*\}", text, flags=re.DOTALL)
        ] or [text]

        data = None
        for candidate in reversed(json_candidates):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                data = parsed
                break

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
            if not 0.0 <= value <= 1.0:
                return {}
            scores[capability] = value

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
