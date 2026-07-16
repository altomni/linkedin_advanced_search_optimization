"""Health endpoint for the v3 API."""
from fastapi import APIRouter

import advanced_search_optimization_v3 as aso

router = APIRouter()


@router.get("/health")
def health():
    return {
        "status": "ok",
        "optimizer": "advanced_search_optimization_v3",
        "max_archetypes": aso.MAX_ARCHETYPES,
        "widen_top_k": aso.ASO_V3_WIDEN_TOP_K,
    }
