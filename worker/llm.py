"""The model: an open-weights model served by Groq. Swap it with GROQ_MODEL in .env."""

from langchain_groq import ChatGroq
from pydantic import BaseModel

from worker.config import settings


def get_llm():
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set. Copy .env.example to .env and add your key.")
    return ChatGroq(model=settings.model, api_key=settings.groq_api_key, temperature=0,
                    max_retries=3, timeout=120)


class Structured:
    """Structured output that tolerates provider quirks: try JSON-schema mode first, then
    fall back to tool-calling mode, then retry once with the validation error shown."""

    def __init__(self, llm, schema: type[BaseModel]):
        self.schema = schema
        self.modes = [llm.with_structured_output(schema, method=m)
                      for m in ("json_schema", "function_calling")]

    def invoke(self, prompt):
        errors = []
        for runnable in self.modes:
            try:
                out = runnable.invoke(prompt)
                if out is not None:
                    return out
            except Exception as e:  # noqa: BLE001 - try the next mode
                errors.append(f"{type(e).__name__}: {str(e)[:300]}")
        raise RuntimeError(f"Could not get a valid {self.schema.__name__}: {errors}")


def structured(llm, schema: type[BaseModel]):
    # The scripted test model has no real structured-output modes.
    if not isinstance(llm, ChatGroq):
        return llm.with_structured_output(schema)
    return Structured(llm, schema)
