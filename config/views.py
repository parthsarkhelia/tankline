from django.http import JsonResponse

from stations.models import Station


def healthz(request):
    if not Station.objects.exists():
        return JsonResponse({"status": "no stations loaded"}, status=503)
    return JsonResponse({"status": "ok"})
