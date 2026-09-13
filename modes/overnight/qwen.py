import json
import os
import re
from typing import Any

import requests


class QwenOllamaError(RuntimeError):
    pass


class QwenAnalyzer:
    """
    Production Ollama/Qwen analyzer.

    Qwen receives:
      - the complete timestamped transcript
      - the heuristic candidate windows
      - SCAG editorial rules

    It returns strict JSON selections that are converted into render jobs.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = 600,
    ):
        self.base_url = (
            base_url
            or os.getenv("SCAG_OLLAMA_URL")
            or "http://localhost:11434"
        ).rstrip("/")
        self.model = (
            model
            or os.getenv("SCAG_QWEN_MODEL")
            or "qwen3:8b"
        )
        self.timeout = timeout

    @staticmethod
    def _system_prompt() -> str:
        return """
You are SCAG's senior gaming-content editor.

Your job is to inspect an ENTIRE timestamped livestream transcript and a
list of automatically detected candidate windows.

Do NOT select moments merely because a keyword appears.

Judge moments using:
- entertainment value
- genuine reaction/emotion
- clutch/high-skill gameplay described by dialogue
- comedy
- surprise
- story/context
- strong opinions or statements
- viewer curiosity
- standalone clip potential
- whether the moment makes sense without requiring excessive context

You must reason over the entire transcript so that earlier/later dialogue can
explain what happened inside a candidate window.

For each selected moment:
1. choose the actual core moment boundaries;
2. do not invent timestamps;
3. use timestamps from the supplied transcript/candidates;
4. give a concise reason;
5. score it from 0 to 100;
6. decide whether it is suitable for a short;
7. decide whether it is suitable for long-form extraction;
8. identify the category.

Return ONLY valid JSON matching this schema:

{
  "selections": [
    {
      "candidate_id": 1,
      "start": 123.45,
      "end": 145.67,
      "score": 94,
      "category": "clutch|reaction|funny|rage|surprise|story|opinion|other",
      "render_short": true,
      "render_long": false,
      "reason": "Why this is strong content.",
      "context_note": "Any important context for editing."
    }
  ]
}

Do not output markdown.
Do not output commentary outside JSON.
""".strip()

    def _extract_json(self, text: str) -> dict[str, Any]:
        text = text.strip()

        # Remove common markdown fencing if a model adds it despite instructions.
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if not match:
                raise QwenOllamaError(
                    "Qwen returned non-JSON output."
                )
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise QwenOllamaError(
                    f"Could not parse Qwen JSON: {exc}"
                ) from exc

        if not isinstance(data, dict):
            raise QwenOllamaError("Qwen response must be a JSON object.")

        selections = data.get("selections")
        if not isinstance(selections, list):
            raise QwenOllamaError(
                "Qwen JSON is missing a 'selections' list."
            )

        return data

    def _validate(self, data: dict[str, Any], candidates) -> list[dict]:
        candidate_ids = {c.candidate_id for c in candidates}
        clean = []

        for item in data["selections"]:
            if not isinstance(item, dict):
                continue

            cid = item.get("candidate_id")
            if cid not in candidate_ids:
                continue

            try:
                start = float(item["start"])
                end = float(item["end"])
                score = float(item["score"])
            except (KeyError, TypeError, ValueError):
                continue

            if start < 0 or end <= start:
                continue

            score = max(0.0, min(100.0, score))

            clean.append({
                "candidate_id": cid,
                "start": start,
                "end": end,
                "score": score,
                "category": str(item.get("category", "other")),
                "render_short": bool(item.get("render_short", True)),
                "render_long": bool(item.get("render_long", False)),
                "reason": str(item.get("reason", "")),
                "context_note": str(item.get("context_note", "")),
            })

        clean.sort(key=lambda x: x["score"], reverse=True)
        return clean

    def analyze(self, transcript_text: str, candidates) -> list[dict]:
        candidate_payload = [
            {
                "candidate_id": c.candidate_id,
                "start": c.start,
                "end": c.end,
                "score": c.score,
                "signals": c.signals,
                "reason": c.reason,
            }
            for c in candidates
        ]

        user_prompt = f"""
FULL TIMESTAMPED TRANSCRIPT:

{transcript_text}

AUTOMATIC CANDIDATE WINDOWS:

{json.dumps(candidate_payload, indent=2)}

Review the complete transcript before deciding which candidates deserve
editing. Return the strongest moments only.
""".strip()

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1,
            },
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise QwenOllamaError(
                f"Could not connect to Ollama at {self.base_url}. "
                f"Make sure Ollama is running and the Qwen model "
                f"'{self.model}' is installed. Error: {exc}"
            ) from exc

        try:
            body = response.json()
            content = body["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise QwenOllamaError(
                f"Unexpected Ollama response: {response.text[:1000]}"
            ) from exc

        return self._validate(
            self._extract_json(content),
            candidates,
        )
