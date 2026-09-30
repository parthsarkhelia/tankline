from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, register
from django.core.checks import Warning as CheckWarning

MAX_STOP_COST_USD = 1000
MAX_DETOUR_COST_PER_MILE_USD = 100


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


@register()
def stop_cost_setting(**_kwargs):
    if not settings.STOP_COST_USD.is_finite() or not 0 <= settings.STOP_COST_USD <= MAX_STOP_COST_USD:
        return [
            Error(
                f"STOP_COST_USD must be between 0 and {MAX_STOP_COST_USD} dollars (larger is unrealistic "
                "and would overflow the optimiser's integer keys).",
                id="planner.E003",
            )
        ]
    return []


@register()
def detour_cost_setting(**_kwargs):
    cost = settings.DETOUR_COST_PER_MILE_USD
    if not cost.is_finite() or not 0 <= cost <= MAX_DETOUR_COST_PER_MILE_USD:
        return [
            Error(
                f"DETOUR_COST_PER_MILE_USD must be between 0 and {MAX_DETOUR_COST_PER_MILE_USD} dollars "
                "(larger is unrealistic and would overflow the optimiser's integer keys).",
                id="planner.E004",
            )
        ]
    return []
