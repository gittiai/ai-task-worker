"""The model. Grok via xAI; swap the model name in .env."""

from langchain_xai import ChatXAI

from worker.config import settings


def get_llm():
    if not settings.xai_api_key:
        raise RuntimeError("XAI_API_KEY is not set. Copy .env.example to .env and add your key.")
    return ChatXAI(model=settings.model, api_key=settings.xai_api_key, temperature=0,
                   max_retries=3, timeout=120)
