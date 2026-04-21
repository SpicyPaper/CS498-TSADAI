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
    ) -> None:
        self.agent = OllamaAgent(
            model=model,
            host=host,
            system_prompt=(
                "You are a query router. "
                "Choose exactly one capability from: general, math, programming, writing, summarization, research, planning, creative. "
                "general = casual conversation, simple facts, broad explanations, everyday advice, ambiguous queries. "
                "math = arithmetic, equations, calculations, formulas, proofs, probability, statistics. "
                "programming = code, Python, functions, APIs, scripts, algorithms, debugging, software. "
                "writing = rewrite, grammar, translation, email, essay, report, documentation prose. "
                "summarization = summarize, shorten, extract key points, notes, condense provided text. "
                "research = compare, investigate, explain a topic, background, sources, citations, current facts. "
                "planning = plan, schedule, roadmap, strategy, checklist, steps, study plan. "
                "creative = story, poem, joke, brainstorm, names, slogans, characters, dialogue. "
                "Important priority: if the query asks to write code, a Python function, a script, an API, an algorithm, or mentions a programming language, choose programming, not writing. "
                "Return only the capability name. "
                "If uncertain, return general. "
                "Do not explain."
            ),
            num_predict=8,
            temperature=0.0,
            think=False,
        )

    async def classify(self, prompt: str) -> str:
        raw = await self.agent.generate(f"Query: {prompt}\nCapability:")

        capability = self._parse_capability(raw)
        if capability in CAPABILITIES:
            return capability

        return "general"

    def _parse_capability(self, raw: str) -> str | None:
        lowered = raw.strip().lower()

        if lowered in CAPABILITIES:
            return lowered

        words = lowered.replace(":", " ").replace(",", " ").replace(".", " ").split()
        matches = [capability for capability in CAPABILITIES if capability in words]

        if len(matches) == 1:
            return matches[0]

        return None
