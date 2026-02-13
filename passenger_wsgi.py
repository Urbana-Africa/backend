import sys, os

# Passenger on cPanel runs the default python, which is python 2
# so the first thing we do is force a switch to python3 in the virtualenv
INTERP = f"/var/www/clients/client0/web5/web/venv/bin/python3.12"

# sys.executable is the path of the running Python
# Check to see that the correct Python version is running. The first
# time this runs, it won't be! Second time round, it should be.
# First argument to os.execl is the program to execute (INTERP);
# the second argument is the first part of the arguments to it, which
# is the full path to the executable itself.
# sys.argv is the rest of the arguments originally passed to the INTERP program.

if sys.executable != INTERP: os.execl(INTERP, INTERP, *sys.argv)

# The arguments to setdefault must match the configuration in the main() function in your manage.py file

environ=os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'urbana.settings')

# Import the app variable from your Django project wsgi file

SCRIPT_NAME = ''

class PassengerPathInfoFix(object):
    """
    Sets PATH_INFO from REQUEST_URI because Passenger doesn't provide it.
    """
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        from urllib.parse import unquote
        environ['SCRIPT_NAME'] = SCRIPT_NAME

        request_uri = unquote(environ['REQUEST_URI'])
        script_name = unquote(environ.get('SCRIPT_NAME', ''))
        offset = request_uri.startswith(script_name) and len(environ['SCRIPT_NAME']) or 0
        environ['PATH_INFO'] = request_uri[offset:].split('?', 1)[0]
        return self.app(environ, start_response)

from urbana.wsgi import application
application = application
application = PassengerPathInfoFix(application)
