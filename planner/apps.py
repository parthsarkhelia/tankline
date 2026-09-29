from django.apps import AppConfig


class PlannerConfig(AppConfig):
    name = "planner"

    def ready(self):
        from . import checks  # noqa: F401  registers system checks
