"""Plan the demo routes once so the Postman demo is served from the cache."""

from django.core.management.base import BaseCommand, CommandError

from planner.errors import PlannerError
from planner.services import plan_trip

# Keep in sync with postman/tankline.postman_collection.json.
DEMO_ROUTES = [
    ("Chicago, IL", "Denver, CO"),
    ("New York, NY", "Los Angeles, CA"),
    ("10001", "34.0522,-118.2437"),
    ("Chicago, IL", "St. Louis, MO"),
]


class Command(BaseCommand):
    help = "Plan the demo routes once so later requests make no routing calls."

    def handle(self, *args, **options):
        failed = 0
        for start, finish in DEMO_ROUTES:
            try:
                body = plan_trip(start, finish)
            except PlannerError as exc:
                failed += 1
                self.stderr.write(f"{start} -> {finish}: FAILED ({exc.code})")
                continue
            meta = body["meta"]
            self.stdout.write(
                f"{start} -> {finish}: {body['route']['provider']}, "
                f"{meta['external_calls']} calls, cache_hit={meta['cache_hit']}"
            )
        if failed:
            raise CommandError(f"{failed} demo route(s) failed.")
