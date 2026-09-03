"""
workers/tasks.py — Background jobs (Phase 2, not yet wired).

The autonomous ordering agent will run here rather than inline in a request:
placing a Dineout booking, a Food order and an Instamart checkout can each
take seconds and must survive the HTTP request that triggered them.

Nothing in the app imports this module yet — there is no Celery app
instance, no broker wiring, and `celery` in requirements.txt is a
forward-looking dependency. When Phase 2 starts:

  1. create the Celery app (broker = settings.REDIS_URL)
  2. implement place_all_orders(plan_id) here — read the approved plan,
     call book_table / place_food_order / checkout, write the returned
     ids back onto the Plan row, advance PlanStatus
  3. enqueue it from POST /plans/{plan_id}/order

Kept as an explicit placeholder so the intent is documented in one place.
"""

__all__: list[str] = []
