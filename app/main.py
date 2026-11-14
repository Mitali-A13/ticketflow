"""FastAPI app: ingest, job status, ops endpoints, browser pages."""

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal, get_args

from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.auth import COOKIE_NAME, LoginRequired, api_auth, browser_auth, valid_key
from app.db import connect, init_db
from app.demo import router as demo_router
from app.logs import setup_logging
from app.models import (
    Category,
    EmailIn,
    EnqueueOut,
    Extraction,
    JobListItem,
    JobOut,
    MetricsOut,
    Urgency,
)
from app.ops import get_metrics, list_jobs, retry_job
from app.queue import enqueue_email, get_job, get_result
from app.ratelimit import RateLimiter
from app.reviews import EDITABLE, get_result_by_id, pending_reviews, save_review
from app.ui import BASE, render, templates

log = logging.getLogger("api")

JobStatus = Literal["queued", "running", "succeeded", "needs_review", "dead"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    setup_logging()
    if not os.environ.get("TICKETFLOW_API_KEY", "").strip():
        log.error("TICKETFLOW_API_KEY is not set: every request will be rejected")
    app.state.limiter = RateLimiter(
        limit=int(os.environ.get("RATE_LIMIT_PER_MIN", "60")), window=60.0
    )
    conn = connect()
    init_db(conn)
    conn.close()
    yield


app = FastAPI(title="TicketFlow", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
app.include_router(demo_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    log.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round((time.perf_counter() - start) * 1000, 1),
        },
    )
    return response


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    return RedirectResponse("/review/login", status_code=303)


def get_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


# ------------------------------------------------------------------ open
@app.get("/health")
def health(conn=Depends(get_conn)):
    conn.execute("SELECT 1")
    return {"status": "ok"}


# ------------------------------------------------------------- API (header key)
@app.post(
    "/emails",
    status_code=202,
    response_model=EnqueueOut,
    dependencies=[Depends(api_auth)],
)
def post_email(
    payload: EmailIn,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=200)
    ],
    conn=Depends(get_conn),
):
    job_id, created = enqueue_email(
        conn,
        idempotency_key,
        payload.sender,
        payload.subject,
        payload.body,
        now=time.time(),
    )
    if not created:
        response.status_code = 200  # same key again: return the original job
    job = get_job(conn, job_id)
    return EnqueueOut(job_id=job_id, status=job["status"], duplicate=not created)


@app.get("/jobs", response_model=list[JobListItem], dependencies=[Depends(api_auth)])
def read_jobs(
    status: JobStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    conn=Depends(get_conn),
):
    return list_jobs(conn, status=status, limit=limit)


@app.get("/jobs/{job_id}", response_model=JobOut, dependencies=[Depends(api_auth)])
def read_job(job_id: int, conn=Depends(get_conn)):
    job = get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    job["result"] = get_result(conn, job_id)  # None until the job has finished
    return job


@app.post(
    "/jobs/{job_id}/retry", response_model=JobOut, dependencies=[Depends(api_auth)]
)
def retry(job_id: int, conn=Depends(get_conn)):
    outcome = retry_job(conn, job_id, now=time.time())
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="job not found")
    if outcome == "not_dead":
        raise HTTPException(status_code=409, detail="only dead jobs can be retried")
    return get_job(conn, job_id)


@app.get("/metrics", response_model=MetricsOut, dependencies=[Depends(api_auth)])
def metrics(conn=Depends(get_conn)):
    return get_metrics(conn)


# ----------------------------------------------- login, logout, review screen
# The login route is declared BEFORE /review/{result_id} so "login" is never
# mistaken for a result id.
@app.get("/review/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/review/login")
def login_submit(request: Request, key: Annotated[str, Form()] = ""):
    if not valid_key(key):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "That key was not accepted."},
            status_code=401,
        )
    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        key,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=8 * 3600,
    )
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/review/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


def _review_page(
    request: Request, conn, error: str | None = None, status_code: int = 200
):
    return render(
        request,
        "review.html",
        {
            "active": "review",
            "items": pending_reviews(conn),
            "error": error,
            "categories": get_args(Category),
            "urgencies": get_args(Urgency),
        },
        status_code=status_code,
    )


@app.get("/review")
def review_page(
    request: Request, conn=Depends(get_conn), key: str = Depends(browser_auth)
):
    return _review_page(request, conn)


@app.post("/review/{result_id}")
def submit_review(
    result_id: int,
    request: Request,
    action: Annotated[str, Form()],
    category: Annotated[str, Form()] = "",
    urgency: Annotated[str, Form()] = "",
    customer_name: Annotated[str, Form()] = "",
    order_id: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    suggested_reply: Annotated[str, Form()] = "",
    conn=Depends(get_conn),
    key: str = Depends(browser_auth),
):
    stored = get_result_by_id(conn, result_id)
    if stored is None:
        return _review_page(request, conn, "That result does not exist.", 404)

    corrected = None
    if action == "corrected":
        try:
            new = Extraction(
                category=category,
                urgency=urgency,
                customer_name=customer_name,
                order_id=order_id,
                # browsers send textarea line breaks as \r\n
                summary=summary.replace("\r\n", "\n"),
                suggested_reply=suggested_reply.replace("\r\n", "\n"),
                confidence=stored["confidence"] or 0.0,
            )
        except ValidationError:
            return _review_page(
                request,
                conn,
                "Invalid values: check category, urgency, summary and reply.",
                400,
            )
        corrected = {
            f: getattr(new, f) for f in EDITABLE if getattr(new, f) != stored[f]
        }
        if not corrected:
            return _review_page(
                request,
                conn,
                "You chose Save corrections but changed nothing. Use Approve instead.",
                400,
            )
    elif action != "approved":
        return _review_page(request, conn, "Unknown action.", 400)

    outcome = save_review(conn, result_id, action, corrected, now=time.time())
    if outcome == "ok":
        return RedirectResponse("/review", status_code=303)
    problems = {
        "not_found": ("That result does not exist.", 404),
        "not_reviewable": ("Only results flagged for review can be reviewed.", 409),
        "already_reviewed": ("That result was already reviewed.", 409),
    }
    message, code = problems[outcome]
    return _review_page(request, conn, message, code)
