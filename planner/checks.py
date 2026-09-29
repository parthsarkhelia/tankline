from django.conf import settings
from django.core.checks import Error, register


@register()
def routing_settings(app_configs, **kwargs):
    if settings.ROUTING_PROVIDER not in {"ors", "osrm"}:
        return [Error("ROUTING_PROVIDER must be 'ors' or 'osrm'.", id="planner.E002")]
    if settings.ROUTING_PROVIDER == "ors" and not settings.ORS_API_KEY:
        return [
            Error(
                "ORS_API_KEY is not set.",
                hint="Add it to .env, or set ROUTING_PROVIDER=osrm to use the public OSRM server only.",
                id="planner.E001",
            )
        ]
    return []
