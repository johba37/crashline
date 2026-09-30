"""The service: FastAPI app, one process with the API and the indexer thread.

    backend/run.sh          # uvicorn app.main:app on 127.0.0.1:$BACKEND_PORT (8650)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .chain import Revert, RpcError
from .service import SERVICE, ApiError
from . import api_catalog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    SERVICE.start()
    yield
    SERVICE.stop()


app = FastAPI(title="Surrogate Pricer backend", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(api_catalog.router)


@app.exception_handler(ApiError)
async def api_error(request: Request, e: ApiError):
    return JSONResponse(e.to_json(), status_code=e.status)


@app.exception_handler(Revert)
async def revert_error(request: Request, e: Revert):
    return JSONResponse(e.to_json(), status_code=409)


@app.exception_handler(RpcError)
async def rpc_error(request: Request, e: RpcError):
    return JSONResponse({"error": "RpcError", "args": {"code": e.code, "message": e.message}}, status_code=502)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, e: RequestValidationError):
    errs = [{"loc": list(x.get("loc", [])), "msg": x.get("msg")} for x in e.errors()]
    return JSONResponse({"error": "BadRequest", "args": {"errors": errs}}, status_code=400)
