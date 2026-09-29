import os

# Assigned, not setdefault: a real key or ROUTING_PROVIDER in the shell must never reach the tests.
os.environ["DJANGO_SECRET_KEY"] = "test-secret"
os.environ["ORS_API_KEY"] = "test-key"
os.environ["ROUTING_PROVIDER"] = "ors"  # every provider call in tests is a recorded response
os.environ.pop("POSTGRES_HOST", None)
os.environ.pop("REDIS_URL", None)

# The environment must be set before the base settings load.
from .settings import *

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": {"anon": "10000/min"}}
