from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import (
    documents,
    job_postings,
    job_sources,
    matching,
    profile,
    resumes,
    target_roles,
    users,
    work_history,
)

app = FastAPI(title="Job Application Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router)
app.include_router(profile.router)
app.include_router(target_roles.router)
app.include_router(resumes.router)
app.include_router(work_history.router)
app.include_router(documents.router)
app.include_router(job_sources.router)
app.include_router(job_postings.router)
app.include_router(matching.router)


@app.get("/health")
def health():
    return {"status": "ok"}
