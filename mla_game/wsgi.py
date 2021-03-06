"""
WSGI config for mla_game project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/1.8/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application
from django.conf import settings

if hasattr(settings, 'NEWRELIC_CONFIG_PATH'):
    import newrelic.agent
    newrelic.agent.initialize(
        settings.NEWRELIC_CONFIG_PATH, settings.NEWRELIC_ENV
    )

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mla_game.settings")

application = get_wsgi_application()
