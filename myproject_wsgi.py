import sys
import os
import asyncio

BASE_DIR = '/home/tupham/remove_pythonanywhere'
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault('PYTHONPATH', BASE_DIR)

from main import app as asgi_app

STATUS_CODES = {
    200: 'OK', 201: 'Created', 204: 'No Content',
    301: 'Moved Permanently', 302: 'Found', 303: 'See Other',
    304: 'Not Modified', 307: 'Temporary Redirect', 308: 'Permanent Redirect',
    400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden',
    404: 'Not Found', 405: 'Method Not Allowed', 422: 'Unprocessable Entity',
    500: 'Internal Server Error', 502: 'Bad Gateway', 503: 'Service Unavailable',
}


def application(environ, start_response):
    """Bridge WSGI → ASGI dùng asyncio.run() cho mỗi request."""
    method = environ.get('REQUEST_METHOD', 'GET')
    path = environ.get('PATH_INFO', '/')
    query_string = environ.get('QUERY_STRING', '').encode('latin-1')
    server_name = environ.get('SERVER_NAME', 'localhost')
    server_port = int(environ.get('SERVER_PORT', 80))

    # Build ASGI headers from WSGI environ
    headers = []
    for key, value in environ.items():
        if key.startswith('HTTP_'):
            name = key[5:].lower().replace('_', '-').encode('latin-1')
            headers.append((name, value.encode('latin-1')))
        elif key in ('CONTENT_TYPE', 'CONTENT_LENGTH') and value:
            name = key.lower().replace('_', '-').encode('latin-1')
            headers.append((name, value.encode('latin-1')))

    content_length = int(environ.get('CONTENT_LENGTH') or 0)
    body = environ['wsgi.input'].read(content_length) if content_length > 0 else b''

    scope = {
        'type': 'http',
        'asgi': {'version': '3.0', 'spec_version': '2.3'},
        'http_version': environ.get('SERVER_PROTOCOL', 'HTTP/1.1').split('/')[-1],
        'method': method,
        'path': path,
        'raw_path': path.encode('latin-1'),
        'query_string': query_string,
        'root_path': environ.get('SCRIPT_NAME', ''),
        'headers': headers,
        'server': (server_name, server_port),
        'client': None,
    }

    result = {'status': 500, 'headers': [], 'body': b''}

    async def run_request():
        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}

        async def send(message):
            if message['type'] == 'http.response.start':
                result['status'] = message['status']
                result['headers'] = list(message.get('headers', []))
            elif message['type'] == 'http.response.body':
                result['body'] += message.get('body', b'')

        await asgi_app(scope, receive, send)

    asyncio.run(run_request())

    status_text = STATUS_CODES.get(result['status'], 'Unknown')
    response_headers = [
        (k.decode('latin-1'), v.decode('latin-1'))
        for k, v in result['headers']
    ]
    start_response(f"{result['status']} {status_text}", response_headers)
    return [result['body']]