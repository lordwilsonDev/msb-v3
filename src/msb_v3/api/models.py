"""Model backend router — list + switch between Ollama and llama.cpp."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

from msb_v3.core.config import settings
from msb_v3.local_ai.llama_client import LlamaCPPClient
from msb_v3.local_ai.ollama import LocalAIClient

router = APIRouter()

_active_backend: str = "ollama"


class ModelInfo(BaseModel):
    name: str
    backend: str
    active: bool


class SwitchRequest(BaseModel):
    backend: str


def _list_models() -> List[Dict[str, object]]:
    return [
        {
            "name": settings.ollama_model,
            "backend": "ollama",
            "active": _active_backend == "ollama",
        },
        {
            "name": settings.llama_cpp_model,
            "backend": "llamacpp",
            "active": _active_backend == "llamacpp",
        },
    ]


def _switch_backend(req: SwitchRequest | Dict[str, Any]) -> Dict[str, str]:
    global _active_backend
    if isinstance(req, dict):
        backend = req.get("backend", "").lower()
    else:
        backend = req.backend.lower()
    if backend not in {"ollama", "llamacpp"}:
        return {"status": "error", "message": f"unknown backend: {backend}"}
    _active_backend = backend
    return {"status": "ok", "backend": backend}


@router.get("/", response_model=List[ModelInfo])
async def list_models_endpoint() -> List[Dict[str, object]]:
    return _list_models()


@router.post("/switch")
async def switch_backend_endpoint(req: SwitchRequest) -> Dict[str, str]:
    return _switch_backend(req)


def get_active_backend() -> str:
    return _active_backend


def get_client() -> LocalAIClient | LlamaCPPClient:
    if _active_backend == "llamacpp":
        return LlamaCPPClient()
    return LocalAIClient()
