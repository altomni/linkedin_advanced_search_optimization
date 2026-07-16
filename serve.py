"""Deployment server for linkedin_advanced_search_optimization.

Mounts the v3 API routers at /v3 and serves with uvicorn:

    python serve.py                 # http://127.0.0.1:5178
    PORT=8080 python serve.py       # custom port
    ASO_V3_HOST=0.0.0.0 python serve.py   # expose beyond localhost

Endpoints:
    GET  /v3/health
    POST /v3/optimize-search        (parsing_result -> optimized archetype conditions)
    POST /v3/optimize-and-fetch     (JD/conditions -> union candidate records + stats)
    Swagger UI at /docs

Consumers: JDSearchAgent's src/graphs_v2/whole_pipeline_v2_standalone_streamlit.py calls
/v3/optimize-and-fetch (set ASO_V3_SERVICE_URL if not on the default port).
"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"))

from fastapi import FastAPI

from api.v3.endpoints.health import router as health_router
from api.v3.endpoints.optimize_search import router as optimize_search_router
from api.v3.endpoints.optimize_and_fetch import router as optimize_and_fetch_router

app = FastAPI(
    title="linkedin_advanced_search_optimization deployment",
    description="advanced_search_optimization_v3 multi-archetype optimizer + pipelined "
                "per-condition candidate fetch, served as REST.",
    version="1.0",
)
app.include_router(health_router, prefix="/v3", tags=["Health"])
app.include_router(optimize_search_router, prefix="/v3", tags=["Optimize"])
app.include_router(optimize_and_fetch_router, prefix="/v3", tags=["Optimize + Fetch"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app,
                host=os.getenv("ASO_V3_HOST", "127.0.0.1"),
                port=int(os.getenv("PORT", "5178")),
                log_level="info")