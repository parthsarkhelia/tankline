from django.apps import AppConfig


class PlannerConfig(AppConfig):
    name = "planner"

    def ready(self):
        # Deliberately lazy: registers the system checks once the app registry is ready.
        from . import checks  # noqa: F401  # pylint: disable=import-outside-toplevel,unused-import
