"""
Swappable LLM client.
Set LLM_PROVIDER=gemini|openai|ollama in .env
All agents import from here — never import langchain models directly in agents.
"""

import os
import base64
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()


def _get_gemini_client(vision: bool = False):
    from langchain_google_genai import ChatGoogleGenerativeAI
    model = "gemini-1.5-flash" if not vision else "gemini-1.5-flash"  # flash handles vision too
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=os.getenv("GEMINI_API_KEY"),
        temperature=0.0,
    )


def _get_openai_client(vision: bool = False):
    from langchain_openai import ChatOpenAI
    model = "gpt-4o" if vision else "gpt-4o-mini"
    return ChatOpenAI(
        model=model,
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.0,
    )


def _get_ollama_client(vision: bool = False):
    from langchain_ollama import ChatOllama
    model = os.getenv("OLLAMA_VISION_MODEL", "llava") if vision else os.getenv("OLLAMA_TEXT_MODEL", "llama3.2")
    return ChatOllama(
        model=model,
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0.0,
    )


def get_llm(vision: bool = False):
    """Return LLM client based on LLM_PROVIDER env var."""
    if PROVIDER == "gemini":
        return _get_gemini_client(vision)
    elif PROVIDER == "openai":
        return _get_openai_client(vision)
    elif PROVIDER == "ollama":
        return _get_ollama_client(vision)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {PROVIDER}. Use gemini|openai|ollama")


def encode_image_to_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def encode_pdf_to_base64(pdf_path: str) -> str:
    with open(pdf_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def build_vision_message(file_path: str, prompt: str) -> list:
    """
    Build a multimodal message for vision LLMs.
    Handles PDF and image inputs.
    Returns list of message dicts compatible with langchain invoke.
    """
    from langchain_core.messages import HumanMessage

    suffix = Path(file_path).suffix.lower()

    if suffix == ".pdf":
        # Gemini and OpenAI support PDF as base64
        if PROVIDER in ("gemini", "openai"):
            b64 = encode_pdf_to_base64(file_path)
            content = [
                {
                    "type": "text",
                    "text": prompt
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:application/pdf;base64,{b64}"
                    }
                }
            ]
        else:
            # Ollama doesn't support PDF natively — caller must extract text first
            content = [{"type": "text", "text": prompt}]

    elif suffix in (".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif"):
        b64 = encode_image_to_base64(file_path)
        mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else f"image/{suffix[1:]}"
        content = [
            {
                "type": "text",
                "text": prompt
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{b64}"
                }
            }
        ]
    else:
        # Fallback: text only
        content = [{"type": "text", "text": prompt}]

    return [HumanMessage(content=content)]


def get_provider_info() -> dict:
    return {
        "provider": PROVIDER,
        "vision_capable": PROVIDER in ("gemini", "openai", "ollama"),
        "note": "ollama vision requires llava model pulled locally"
    }
