"""
tests/conftest.py — Shared test fixtures.

CONCEPT: pytest fixtures
--------------------------
A fixture is a function that sets up test dependencies and tears them
down after. pytest injects fixtures into test functions automatically
by matching parameter names.

@pytest.fixture
def some_fixture():
    # setup
    yield value   # value is injected into the test
    # teardown (after yield)

CONCEPT: Why we mock MCP clients in tests
-------------------------------------------
Real MCP calls require Swiggy credentials and network access.
Tests must be:
  - Fast (no network)
  - Deterministic (same result every run)
  - Safe (no real orders placed)

We use respx to mock HTTP calls and unittest.mock to replace
MCP client methods with controlled fake responses.

CONCEPT: Test database
------------------------
We use a separate in-memory SQLite DB for tests instead of Postgres.
SQLite is file-based and requires no Docker container.
SQLModel works with both — same code, different connection string.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


# ── Shared event data fixture ────────────────────────────────────────────────

@pytest.fixture
def sample_event_data():
    """
    Standard event config used across multiple tests.
    A simple date night in Lucknow — covers the most common case.
    """
    return {
        "event_type": "date",
        "venue_mode": "hybrid",
        "location": "Lucknow",
        "start_hour": 20,
        "budget": 3000,
        "guest_count": 2,
        "guests": [],
        "dietary_tags": [],
        "health_focus": 50,
        "notes": None,
    }


@pytest.fixture
def sample_mcp_context():
    """
    Simulated MCP orchestrator output.
    Mirrors the shape returned by MCPOrchestrator.gather_context().
    Used to test the AI planner without hitting real MCP servers.
    """
    return {
        "food": {
            "restaurants": [
                {
                    "id": "rest_001",
                    "name": "Meghana Foods",
                    "cuisine": "Andhra",
                    "rating": 4.5,
                    "delivery_time_mins": 35,
                    "price_for_two": 400,
                    "offers": [{"code": "SWIGGY50", "description": "50% off up to ₹100"}],
                    "top_dishes": [
                        {"name": "Biryani", "price": 220, "is_bestseller": True},
                        {"name": "Pepper Chicken", "price": 280, "is_bestseller": False},
                    ],
                }
            ],
            "total_results": 1,
        },
        "instamart": {
            "categories": [
                {
                    "name": "Ambience",
                    "items": [
                        {"product_id": "im_001", "name": "Tealight Candles", "brand": "Hosley", "price": 149, "unit": "pack", "recommended_qty": 1},
                    ],
                }
            ],
            "estimated_total": 149,
            "delivery_estimate_mins": 15,
        },
        "dineout": {
            "restaurants": [
                {
                    "id": "dine_001",
                    "name": "Farzi Cafe",
                    "cuisine": "Modern Indian",
                    "rating": 4.6,
                    "cost_for_two": 1800,
                    "ambience": ["Rooftop", "Romantic"],
                    "available_slots": ["7:30 PM", "8:00 PM", "8:30 PM"],
                    "offers": [{"type": "pre_booking", "description": "15% off on pre-booking", "code": "EARLYBIRD15"}],
                }
            ],
            "total_results": 1,
        },
        "venue_mode": "hybrid",
        "budget_split": {"dineout": 1500, "food": 1050, "instamart": 450},
    }


@pytest.fixture
def sample_offers():
    """Active offers fixture — mirrors OffersEngine output."""
    return [
        {
            "service": "food",
            "type": "percentage",
            "code": "SWIGGY50",
            "description": "50% off up to ₹100",
            "min_order": 199,
            "max_saving": 100,
            "payment_method": None,
        },
        {
            "service": "dineout",
            "type": "percentage",
            "code": "EARLYBIRD15",
            "description": "15% off on pre-booking",
            "min_order": 500,
            "max_saving": 300,
            "payment_method": None,
        },
    ]