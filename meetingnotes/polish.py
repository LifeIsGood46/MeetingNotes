"""LLM-driven cleanup and structured document generation from a transcript.

Two-phase approach:
  1) clean_transcript  — fix disfluencies, errors, remaining ASR mangling
  2) generate_document — turn the cleaned transcript into a task-specific doc
"""

from __future__ import annotations

from .llm import LLMProvider

_CLEAN_SYSTEM = (
    "You are an expert editor cleaning up an automatic speech-to-text transcript "
    "of a technical meeting. Fix misheard domain terms, punctuation, casing, "
    "disfluencies and obvious ASR errors. Preserve meaning — do not add facts. "
    "Keep the original language. Output cleaned prose with short paragraphs."
)

_TASKS: dict[str, str] = {
    "clean": "Return only the cleaned transcript. No commentary.",
    "notes": (
        "From the cleaned transcript, produce concise meeting notes: summary, "
        "key discussion points, and decisions. Markdown format."
    ),
    "actions": (
        "Extract all action items: owner (if mentioned), task, and any deadlines. "
        "Return a markdown checklist."
    ),
    "testplan": (
        "Turn the cleaned transcript into a structured test plan. Infer features, "
        "scenarios (numbered steps + expected results), and priorities. Markdown."
    ),
    "summary": "Write a 3-6 sentence executive summary of the meeting.",
}

TASK_SYSTEM = (
    "You are a senior technical writer producing documentation from a meeting "
    "transcript. Be structured, accurate, and do not invent details."
)


def clean_transcript(transcript_text: str, llm: LLMProvider) -> str:
    """Phase 1: LLM cleanup of the (already glossary-corrected) transcript."""
    resp = llm.complete(
        system=_CLEAN_SYSTEM,
        user=f"{_TASKS['clean']}\n\n---\n{transcript_text}",
    )
    return resp.text


def generate_document(clean_text: str, task: str, llm: LLMProvider) -> str:
    """Phase 2: produce a task-specific document from cleaned text."""
    instruction = _TASKS.get(task)
    if instruction is None:
        raise ValueError(f"Unknown task '{task}'. Choose from: {', '.join(_TASKS)}")
    resp = llm.complete(system=TASK_SYSTEM, user=f"{instruction}\n\n---\n{clean_text}")
    return resp.text


def generate_custom(clean_text: str, custom_prompt: str, llm: LLMProvider) -> str:
    """Phase 2 variant: run a user-supplied instruction against the transcript."""
    resp = llm.complete(system=TASK_SYSTEM, user=f"{custom_prompt}\n\n---\n{clean_text}")
    return resp.text


def available_tasks() -> list[str]:
    return list(_TASKS)


def task_default_prompts() -> dict[str, str]:
    """Return the built-in instruction text for each structured task.

    Shown to the user (editable) when they pick a preset; ``custom`` has none.
    """
    prompts = {name: instr for name, instr in _TASKS.items() if name != "clean"}
    # `clean` is driven by the cleanup system prompt; expose a friendly stub
    prompts["clean"] = "Clean up the transcript: fix misheard terms, punctuation, casing."
    return prompts
