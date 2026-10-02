"""Model providers. Pick one with LLM_PROVIDER and LLM_MODEL in .env; nothing else changes."""

from pydantic import BaseModel

from worker.config import settings


def get_llm():
    if settings.provider == "groq":
        from langchain_groq import ChatGroq

        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not set in .env.")
        return ChatGroq(model=settings.model, api_key=settings.groq_api_key, temperature=0,
                        max_retries=3, timeout=120)
    if settings.provider == "openai_compatible":
        from langchain_openai import ChatOpenAI

        if not (settings.openai_api_key and settings.openai_base_url):
            raise RuntimeError("Set OPENAI_COMPAT_API_KEY and OPENAI_COMPAT_BASE_URL in .env.")
        return ChatOpenAI(model=settings.model, api_key=settings.openai_api_key,
                          base_url=settings.openai_base_url, temperature=0, max_retries=3,
                          timeout=120)
    raise RuntimeError(f"Unknown LLM_PROVIDER {settings.provider!r}.")


class Structured:
    """Structured output that tolerates provider quirks: try JSON-schema mode first, then
    fall back to tool-calling mode."""

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
    from langchain_core.language_models import BaseChatModel

    # The scripted test model has no real structured-output modes.
    if not isinstance(llm, BaseChatModel):
        return llm.with_structured_output(schema)
    return Structured(llm, schema)
