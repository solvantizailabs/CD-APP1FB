"""Standalone prompt files, per the CTO spec's rag/prompts/ layout - moved out of
inline Python string constants so prompt changes are reviewable without touching code."""
import os

_PROMPTS_DIR = os.path.dirname(__file__)


def load_prompt(filename: str) -> str:
    with open(os.path.join(_PROMPTS_DIR, filename), "r", encoding="utf-8") as f:
        return f.read()
