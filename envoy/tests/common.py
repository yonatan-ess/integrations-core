import json
import os

from datadog_checks.base.utils.common import get_docker_hostname

try:
    from functools import lru_cache
except ImportError:
    from backports.functools_lru_cache import lru_cache


HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DIR = os.path.join(HERE, 'fixtures')

HOST = get_docker_hostname()
PORT = '8001'
INSTANCES = {
    'main': {'stats_url': 'http://{}:{}/stats'.format(HOST, PORT)},
    'included_metrics': {
        'stats_url': 'http://{}:{}/stats'.format(HOST, PORT),
        'metric_whitelist': [r'envoy\.cluster\..*'],
    },
    'excluded_metrics': {
        'stats_url': 'http://{}:{}/stats'.format(HOST, PORT),
        'metric_blacklist': [r'envoy\.cluster\..*'],
    },
    'included_excluded_metrics': {
        'stats_url': 'http://{}:{}/stats'.format(HOST, PORT),
        'included_metrics': [r'envoy\.cluster\.'],
        'excluded_metrics': [r'envoy\.cluster\.out\.'],
    },
    'collect_server_info': {
        'stats_url': 'http://{}:{}/stats'.format(HOST, PORT),
        'collect_server_info': 'false',
    },
}
ENVOY_VERSION = os.getenv('ENVOY_VERSION')


class MockResponse:
    def __init__(self, content, status_code):
        self.content = content
        self.status_code = status_code

    def json(self):
        return json.loads(self.content)


@lru_cache(maxsize=None)
def response(kind):
    file_path = os.path.join(FIXTURE_DIR, kind)
    if os.path.isfile(file_path):
        with open(file_path, 'rb') as f:
            return MockResponse(f.read(), 200)
    else:
        raise IOError('File `{}` does not exist.'.format(file_path))
