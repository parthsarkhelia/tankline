from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .errors import PlannerError
from .serializers import ErrorResponseSerializer, PlanQuerySerializer, PlanResponseSerializer
from .services import plan_trip


class PlanView(APIView):
    @extend_schema(
        parameters=[PlanQuerySerializer],
        responses={
            200: PlanResponseSerializer,
            400: ErrorResponseSerializer,
            422: ErrorResponseSerializer,
            429: ErrorResponseSerializer,
            502: ErrorResponseSerializer,
        },
        examples=[OpenApiExample("Chicago to Denver", value=None, parameter_only=("start", "query"))],
        summary="Cheapest fuel stops between two US places",
    )
    def get(self, request):
        query = PlanQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        body = plan_trip(**query.validated_data)
        map_url = request.build_absolute_uri(f"{reverse('planner:map')}?{urlencode(query.validated_data)}")
        return Response({**body, "map_url": map_url})


def map_view(request):
    # A plain Django view, so DRF throttling would not run on its own; the map can trigger a routing call.
    if not AnonRateThrottle().allow_request(Request(request), None):
        return render(
            request, "planner/map.html", {"error": "Too many requests. Please slow down."}, status=429
        )
    query = PlanQuerySerializer(data=request.GET)
    if not query.is_valid():
        return render(request, "planner/map.html", {"error": "Invalid query parameters."}, status=400)
    try:
        trip = plan_trip(**query.validated_data)
    except PlannerError as exc:
        return render(request, "planner/map.html", {"error": exc.message}, status=exc.status)
    return render(request, "planner/map.html", {"trip": trip})
