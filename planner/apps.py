from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, register
from django.core.checks import Warning as CheckWarning


class PlannerConfig(AppConfig):
    name = "planner"


@register()  # Django imports this module while populating the registry, so no ready() hook is needed
def routing_settings(**_kwargs):
    if settings.ROUTING_PROVIDER not in {"ors", "osrm"}:
        return [Error("ROUTING_PROVIDER must be 'ors' or 'osrm'.", id="planner.E002")]
    if settings.ROUTING_PROVIDER == "ors" and not settings.ORS_API_KEY:
        return [
            CheckWarning(
                "ROUTING_PROVIDER is 'ors' but ORS_API_KEY is not set.",
                hint="Routing will use the public OSRM server; add ORS_API_KEY for truck routing.",
                id="planner.W001",
            )
        ]
    return []
