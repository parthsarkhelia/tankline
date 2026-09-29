from django.conf import settings
from django.core.checks import Error, register
from django.core.checks import Warning as CheckWarning


@register()
def routing_settings(app_configs, **kwargs):  # pylint: disable=unused-argument  # Django passes these by keyword
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
