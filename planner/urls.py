from django.urls import path

from .views import PlanView, map_view

app_name = "planner"
urlpatterns = [
    path("api/v1/plan/", PlanView.as_view(), name="plan"),
    path("map/", map_view, name="map"),
]
