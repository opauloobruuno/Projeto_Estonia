import os
import time
from typing import Any, Dict, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from pipeline import build_report


app = FastAPI(title="Analytics API")

# In-memory cache:
# key: filename (str)
# value: tuple (mtime: float, report: dict)
results_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    """
    Simple timing middleware.

    Measures request processing time, logs it, and injects an
    X-Process-Time header in the response.
    """
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    path = request.url.path
    # Console log for observability
    print(f"INFO: Path {path} took {duration:.4f}s")

    # Include header for clients
    response.headers["X-Process-Time"] = f"{duration:.4f}s"
    return response


@app.get("/health")
def health_check() -> Dict[str, str]:
    """Basic health endpoint."""
    return {"status": "ok"}


def _get_cached_or_build(filename: str) -> Dict[str, Any]:
    """
    Retrieve report from cache if valid; otherwise rebuild via build_report.

    Cache invalidation is based on file modification time (mtime).
    """
    if not os.path.exists(filename):
        raise FileNotFoundError(f"File '{filename}' not found")

    current_mtime = os.path.getmtime(filename)

    cached = results_cache.get(filename)
    if cached is not None:
        cached_mtime, cached_report = cached
        # Invalidate cache if file has changed
        if cached_mtime == current_mtime:
            return cached_report

    # Cache miss or invalid, rebuild
    report = build_report(filename)
    results_cache[filename] = (current_mtime, report)
    return report


@app.get("/report")
def get_report(file: str = "events.csv"):
    """
    Build or fetch a cached analytical report for the given CSV file.

    This endpoint is deliberately synchronous (def, not async def).
    FastAPI will execute it in a thread pool so that the CPU-bound
    Pandas/NumPy workload does not block the main event loop.
    """
    try:
        report = _get_cached_or_build(file)
        return JSONResponse(content=report)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # Generic failure of the pipeline or other internal issue
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline failed: {e}",
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )

