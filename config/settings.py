import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


DEBUG = env("DJANGO_DEBUG", "0") == "1"
SECRET_KEY = env("DJANGO_SECRET_KEY") or ("dev-only-insecure-key" if DEBUG else "")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")
ALLOWED_HOSTS = [
    h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "rest_framework",
    "drf_spectacular",
    "stations",
    "planner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

if env("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": env("POSTGRES_HOST"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "NAME": env("POSTGRES_DB", "tankline"),
            "USER": env("POSTGRES_USER", "tankline"),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

if env("REDIS_URL"):
    CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": env("REDIS_URL")}
    }
else:
    # File-based so development routes survive restarts and don't spend the ORS quota twice.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "OPTIONS": {"MAX_ENTRIES": 10000},
            "LOCATION": BASE_DIR / ".cache" / "django",
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": env("API_THROTTLE_RATE", "30/min")},
    "NUM_PROXIES": 0,  # throttle on REMOTE_ADDR; a spoofed X-Forwarded-For must not mint new buckets
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "planner.exceptions.api_exception_handler",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "tankline",
    "DESCRIPTION": "Cheapest fuel stops for a truck route across the USA.",
    "VERSION": "1.0.0",
    "SWAGGER_UI_DIST": "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.33.0",  # pinned, not @latest
}
# Django's default "same-origin" sends no Referer, and OSM's tile servers refuse requests without one.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "planner": {"handlers": ["console"], "level": "INFO"},
        "django": {"handlers": ["console"], "level": "WARNING"},  # tracebacks of 500s reach the container log
    },
}

# Routing
ROUTING_PROVIDER = env(
    "ROUTING_PROVIDER", "osrm"
)  # "ors" = truck routing, OSRM fallback; "osrm" = no key, no quota
ORS_API_KEY = env("ORS_API_KEY", "").strip()  # a stray newline would make every ORS call fail
ORS_DAILY_BUDGET = int(env("ORS_DAILY_BUDGET", "150"))  # of the free tier's 200/day; beyond it, OSRM answers
ORS_SNAP_RADIUS_M = 5000
HTTP_TIMEOUT = (3.05, 20)
ROUTE_CACHE_SECONDS = 7 * 24 * 3600

# Fuel model
VEHICLE_RANGE_MILES = 500
VEHICLE_MPG = 10
VEHICLE_MIN_FILL_GALLONS = (
    10  # a stop pumps at least this; less only when it is exactly what reaches the destination
)
CORRIDOR_BASE_MILES = 5.0
CORRIDOR_MAX_MILES = 20.0
