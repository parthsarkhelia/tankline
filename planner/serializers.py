import math

from rest_framework import serializers


class PlanQuerySerializer(serializers.Serializer):
    start = serializers.CharField(max_length=120, help_text="'City, ST', ZIP code, or 'lat,lng'.")
    finish = serializers.CharField(max_length=120, help_text="'City, ST', ZIP code, or 'lat,lng'.")
    start_fuel_miles = serializers.FloatField(
        min_value=0, max_value=500, default=0, help_text="Range already in the tank at the start (0-500)."
    )

    def validate_start_fuel_miles(self, value):
        if not math.isfinite(value):
            raise serializers.ValidationError("Must be a number between 0 and 500.")
        return value


class PlaceSerializer(serializers.Serializer):
    query = serializers.CharField()
    name = serializers.CharField()
    lat = serializers.FloatField()
    lng = serializers.FloatField()
    resolved_by = serializers.ChoiceField(["coordinates", "zip", "gazetteer", "geocoder"])


class GeometrySerializer(serializers.Serializer):
    type = serializers.CharField()
    coordinates = serializers.ListField(child=serializers.ListField(child=serializers.FloatField()))


class RouteSerializer(serializers.Serializer):
    distance_miles = serializers.FloatField()
    duration_hours = serializers.FloatField()
    provider = serializers.ChoiceField(["openrouteservice", "osrm"])
    profile = serializers.CharField()
    geometry = GeometrySerializer()


class FuelStopSerializer(serializers.Serializer):
    stop = serializers.IntegerField()
    station_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    country = serializers.CharField()
    price_per_gallon = serializers.DecimalField(max_digits=6, decimal_places=3)
    mile = serializers.FloatField()
    off_route_miles = serializers.FloatField(help_text="Straight line from the route to the station.")
    detour_miles = serializers.FloatField(help_text="Road miles to the pump and back (estimated).")
    gallons = serializers.DecimalField(
        max_digits=7, decimal_places=2, help_text="Pumped here; at most a full tank."
    )
    reserve_gallons = serializers.DecimalField(
        max_digits=7, decimal_places=2, help_text="Reserve used to reach the first stop, repaid there."
    )
    cost = serializers.DecimalField(max_digits=9, decimal_places=2)
    lat = serializers.FloatField()
    lng = serializers.FloatField()
    location_precision = serializers.ChoiceField(["exit", "station", "city"])


class SummarySerializer(serializers.Serializer):
    stops = serializers.IntegerField()
    gallons_purchased = serializers.DecimalField(max_digits=8, decimal_places=2)
    gallons_burned = serializers.DecimalField(max_digits=8, decimal_places=2)
    total_cost = serializers.DecimalField(max_digits=10, decimal_places=2)
    start_fuel_miles = serializers.FloatField()
    range_miles = serializers.IntegerField()
    mpg = serializers.IntegerField()
    stop_cost_usd = serializers.DecimalField(max_digits=8, decimal_places=2)
    detour_cost_per_mile_usd = serializers.DecimalField(max_digits=6, decimal_places=3)
    detour_miles = serializers.FloatField(help_text="All detours to pumps and back.")
    route_miles_driven = serializers.FloatField(help_text="Route distance plus detours.")


class MetaSerializer(serializers.Serializer):
    external_calls = serializers.IntegerField()
    cache_hit = serializers.BooleanField()
    elapsed_ms = serializers.IntegerField()


class PlanResponseSerializer(serializers.Serializer):
    start = PlaceSerializer()
    finish = PlaceSerializer()
    route = RouteSerializer()
    fuel_stops = FuelStopSerializer(many=True)
    summary = SummarySerializer()
    assumptions = serializers.ListField(child=serializers.CharField())
    map_url = serializers.URLField()
    meta = MetaSerializer()


class ErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()


class ErrorResponseSerializer(serializers.Serializer):
    error = ErrorSerializer()
