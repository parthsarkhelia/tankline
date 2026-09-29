class PlannerError(Exception):
    """An expected failure with a stable code; rendered as {"error": {...}} by the API."""

    status = 400
    code = "invalid_request"

    def __init__(self, message, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra


class InvalidRequest(PlannerError):
    pass


class SameLocation(PlannerError):
    code = "same_location"


class LocationNotFound(PlannerError):
    code = "location_not_found"


class AmbiguousLocation(PlannerError):
    code = "ambiguous_location"


class OutsideServiceArea(PlannerError):
    code = "outside_service_area"


class RouteRejected(PlannerError):
    status = 422
    code = "route_not_possible"


class FuelGap(PlannerError):
    status = 422
    code = "fuel_gap"


class UpstreamUnavailable(PlannerError):
    status = 502
    code = "routing_unavailable"
