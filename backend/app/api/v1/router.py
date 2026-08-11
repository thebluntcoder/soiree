"""
api/v1/router.py — Mounts all endpoint routers under /api/v1.

CONCEPT: APIRouter composition
--------------------------------
FastAPI lets you split endpoints across multiple files using APIRouter.
Each file defines its own router with a local prefix and tags.
This file composes them all into one api_router that main.py mounts.

Result:
  POST /api/v1/auth/start
  POST /api/v1/search/           ← NEW: restaurant discovery
  POST /api/v1/plans/generate
  POST /api/v1/plans/chat
  GET  /api/v1/plans/{plan_id}
  POST /api/v1/events/
  ... etc
"""

from fastapi import APIRouter
from app.api.v1.endpoints import plans, events, users, offers, orders, auth
from app.api.v1.endpoints import search

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(events.router, prefix="/events", tags=["events"])
api_router.include_router(plans.router, prefix="/plans", tags=["plans"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(offers.router, prefix="/offers", tags=["offers"])
api_router.include_router(orders.router, prefix="/orders", tags=["orders"])
