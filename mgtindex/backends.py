"""Model backends for Stage 2.

The pipeline is provider-agnostic by construction: the model only ever emits terms
tagged with a chunk id, and page numbers are joined on afterwards from Stage 1. So a
backend is just "text in, list-of-entries out" -- swapping providers touches nothing
downstream.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import requests

# The entry schema, in the OpenAPI subset Vertex accepts. Anthropic takes the Pydantic
# model directly (see generate.py); this hand-rolled copy is for Gemini, which rejects
# the $defs/additionalProperties that Pydantic emits.
ENTRY_PROPS = {
    "chunk_id": {"type": "string"},
    "term": {"type": "string"},
    "parent": {"type": "string"},
    "role": {"type": "string", "enum": ["primary", "mention"]},
    "kind": {"type": "string"},
    "aliases": {"type": "array", "items": {"type": "string"}},
    "see_also": {"type": "array", "items": {"type": "string"}},
}
GEMINI_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": ENTRY_PROPS,
                "required": ["chunk_id", "term", "role", "kind"],
            },
        }
    },
    "required": ["entries"],
}

def vertex_project() -> str:
    """The GCP project to bill. Required -- there is no default, on purpose.

    Resolved lazily rather than at import, because the free render stages import this
    module transitively (web -> ships -> backends) and must keep working for someone
    who has never set up Vertex at all. Only an actual request needs a project.
    """
    project = os.environ.get("VERTEX_PROJECT")
    if not project:
        raise RuntimeError(
            "VERTEX_PROJECT is not set. Stages 2 and 3 call Vertex AI and bill a real GCP "
            "project, so you must name it explicitly:\n"
            "    export VERTEX_PROJECT=your-gcp-project"
        )
    return project

# Keyfile, so the API key never has to be pasted into a terminal that is being recorded.
KEYFILE = os.path.expanduser("~/.config/anthropic/key")


def _anthropic_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        with open(KEYFILE) as fh:
            return fh.read().strip()
    except FileNotFoundError:
        raise SystemExit(
            f"No Anthropic API key. Put it in {KEYFILE} (chmod 600), "
            "or set ANTHROPIC_API_KEY."
        )


class Backend:
    name: str
    price_in: float   # $ per 1M input tokens
    price_out: float  # $ per 1M output tokens

    def generate(self, system: str, user: str) -> tuple[list[dict], dict]:
        """Return (entries, usage). usage has input_tokens / output_tokens."""
        raise NotImplementedError

    def cost(self, usage: dict) -> float:
        return (
            usage["input_tokens"] / 1e6 * self.price_in
            + usage["output_tokens"] / 1e6 * self.price_out
        )


class Claude(Backend):
    def __init__(self, model="claude-opus-4-8", price_in=5.0, price_out=25.0):
        import anthropic
        from pydantic import BaseModel, Field

        class Entry(BaseModel):
            chunk_id: str = Field(description="id of the chunk this entry is drawn from")
            term: str = Field(description="the term a reader would look up")
            parent: str = Field(description="broader term this is a subentry of; empty if top-level")
            role: str = Field(description="'primary' if this passage defines it, else 'mention'")
            kind: str = Field(description="rule|table|example|procedure|term|equipment|ship|career|skill|world|creature|alien")
            aliases: list[str] = Field(description="other names a reader might look this up under")
            see_also: list[str] = Field(description="related terms worth cross-referencing")

        class Entries(BaseModel):
            entries: list[Entry]

        self.name = model
        self.model = model
        self.price_in, self.price_out = price_in, price_out
        self.schema = Entries
        self.client = anthropic.Anthropic(api_key=_anthropic_key())

    def generate(self, system, user):
        r = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=self.schema,
        )
        entries = [e.model_dump() for e in r.parsed_output.entries]
        return entries, {
            "input_tokens": r.usage.input_tokens,
            "output_tokens": r.usage.output_tokens,
        }


class Vertex(Backend):
    """Gemini on Vertex AI. Gemini 3.x lives on the `global` endpoint, 2.5 on regional."""

    _token = None
    _token_at = 0.0

    def __init__(self, model, price_in, price_out, location="global"):
        self.name = model
        self.model = model
        self.price_in, self.price_out = price_in, price_out
        self.location = location
        self.host = (
            "aiplatform.googleapis.com" if location == "global"
            else f"{location}-aiplatform.googleapis.com"
        )

    @property
    def url(self) -> str:
        return (
            f"https://{self.host}/v1/projects/{vertex_project()}/locations/{self.location}"
            f"/publishers/google/models/{self.model}:generateContent"
        )

    @classmethod
    def token(cls) -> str:
        # ADC tokens last ~1h; refresh every 30 min rather than shell out per request
        if cls._token is None or time.time() - cls._token_at > 1800:
            cls._token = subprocess.check_output(
                ["gcloud", "auth", "application-default", "print-access-token"], text=True
            ).strip()
            cls._token_at = time.time()
        return cls._token

    def generate(self, system, user):
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": GEMINI_SCHEMA,
                "maxOutputTokens": 32000,
            },
        }
        for attempt in range(4):
            r = requests.post(
                self.url,
                headers={"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"},
                json=body,
                timeout=300,
            )
            if r.status_code == 200:
                break
            if r.status_code in (429, 503, 500) and attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            raise RuntimeError(f"{self.model} HTTP {r.status_code}: {r.text[:300]}")

        d = r.json()
        cand = d["candidates"][0]
        text = "".join(p.get("text", "") for p in cand["content"]["parts"])
        entries = json.loads(text)["entries"]
        u = d.get("usageMetadata", {})
        return entries, {
            "input_tokens": u.get("promptTokenCount", 0),
            # thinking tokens are billed as output on Gemini -- count them
            "output_tokens": u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0),
        }


def make(arm: str) -> Backend:
    return {
        "opus":       lambda: Claude("claude-opus-4-8", 5.0, 25.0),
        "gemini-pro": lambda: Vertex("gemini-3.1-pro-preview", 2.0, 12.0),
        "gemini-3.5": lambda: Vertex("gemini-3.5-flash", 1.5, 9.0),
        "gemini-flash": lambda: Vertex("gemini-3-flash-preview", 0.5, 3.0),
    }[arm]()
