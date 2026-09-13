from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from purchasing.api.routes import router
from purchasing.application.seeding import seed_demo_data
from purchasing.infrastructure.db import SessionLocal
from purchasing.settings import settings
from purchasing.workflow.graph import ReviewWorkflow, build_graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as session:
        seed_demo_data(session)
    checkpoint_path = Path(settings.checkpoint_db)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        checkpointer.setup()
        app.state.workflow = ReviewWorkflow(build_graph(checkpointer))
        yield


app = FastAPI(title="AI Purchasing Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Idempotency-Key", "X-Demo-Role"],
)
app.include_router(router)


@app.get("/health")
def health(request: Request):
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Database is not ready.") from exc
    if not getattr(request.app.state, "workflow", None):
        raise HTTPException(503, "Workflow checkpoint is not ready.")
    return {
        "status": "ok",
        "database_ready": True,
        "workflow_ready": True,
        "llm_provider": settings.llm_provider,
        "llm_configured": bool(
            {
                "gemini": settings.gemini_api_key,
                "nvidia": settings.nvidia_api_key,
                "groq": settings.groq_api_key,
            }.get(settings.llm_provider.lower())
        ),
    }
