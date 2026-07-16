"""Shared test setup: project root on sys.path; no live LLM / LinkedIn calls in any test."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class FakeLLM:
    """Stands in for ChatGPTWrapper: .invoke() returns a queued (text, in_tok, out_tok)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def invoke(self, prompt, model_type=None, temperature=None, **kw):
        self.prompts.append(prompt)
        text = self.responses.pop(0) if self.responses else "{}"
        return text, 10, 5