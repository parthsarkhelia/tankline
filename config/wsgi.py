"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

# Imports below sit after app setup on purpose.
# Load lookup tables once in the master process (gunicorn --preload); forked workers share them.
from django.db import DatabaseError, connections

from planner.borders import default_borders
from planner.services import station_table
from stations.geo import places_index, zcta_index

places_index()
zcta_index()
default_borders()
try:
    station_table()
except DatabaseError:  # not migrated yet (first local run); the first request loads it
    pass
finally:
    connections.close_all()  # forked workers must not share this connection
