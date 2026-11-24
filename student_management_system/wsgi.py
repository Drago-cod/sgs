"""
WSGI config for student_management_system project.
"""

import logging
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'student_management_system.settings')

application = get_wsgi_application()


def _bootstrap_database():
    """Ensure migrations and initial admin user exist when booting on PaaS (e.g. Render)."""
    if os.environ.get('DISABLE_AUTO_MIGRATE', '').lower() in ('1', 'true', 'yes'):
        return
    try:
        from django.core.management import call_command

        call_command('migrate', '--noinput', verbosity=0)
        call_command('ensure_admin')
    except Exception:
        logging.getLogger('django.request').exception(
            'Automatic WSGI startup migration/seed failed'
        )


_bootstrap_database()

app = application
