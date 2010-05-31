#!/usr/bin/env python
from __future__ import with_statement

import contextlib
import gzip
import StringIO
import time

import eventlet
import eventlet.wsgi

from eventlet.green import httplib
from eventlet.green import urllib2

PROXIED_HEADERS = ('HTTP_USER_AGENT', 'HTTP_ACCEPT_CHARSET', 'HTTP_ACCEPT',
                'HTTP_ACCEPT_LANGUAGE', )#'HTTP_COOKIE', 'HTTP_ACCEPT_CHARSET')
REDIRECT_CODES = (301, 302, 303,)

def wsgi_ok(start_response, output, headers):
    start_response('200 OK', [(k, v) for k, v in headers.iteritems()])
    return [output]

def wsgi_error(start_response, output, headers):
    start_response('500 Server Error', [(k, v) for k, v in headers.iteritems()])
    return [output]

def fetch_from(method, url, headers):
    print '>> Requesting: %s' % url
    start = time.time()
    request = urllib2.Request(url=url, headers=headers)
    try:
        return urllib2.urlopen(request)
    finally:
        print ('fetch_from(%s, %s, ..) took %s' % (method, url, (time.time() - start)))

def wsgi_proxy(env, start_response):
    if not env['wsgi.url_scheme'] == 'http':
        return wsgi_error(start_response, 'Error\r\n', {})

    if not env['REQUEST_METHOD'] == 'GET':
        return wsgi_error(start_response, 'Only GET is suppported\r\n', {})

    # Strip off early 'http://'
    url = env['PATH_INFO']
    headers = dict(((k, env[k]) for k in PROXIED_HEADERS if env.has_key(k)))

    if env['QUERY_STRING']:
        url = '%s?%s' % (url, env['QUERY_STRING'])

    response = fetch_from(env['REQUEST_METHOD'], url, headers)
    headers = dict(response.headers)

    if response.code in REDIRECT_CODES:
        if not headers.get('location'):
            return wsgi_error(start_response, 'No Location header given with redirect code %d\r\n' % response.code, {})
        print ('Redirecting', env['PATH_INFO'], headers['location'])
        env.update({'PATH_INFO' : headers['location']})
        return wsgi_proxy(env, start_response)

    headers.pop('transfer-encoding', None)
    print ('headers', headers)
    response = response.read()

    if response and 'gzip' in env.get('HTTP_ACCEPT_ENCODING', ''):
        headers['Content-Encoding'] = 'gzip'
        start = time.time()
        out = StringIO.StringIO()
        gzout = gzip.GzipFile(None, 'wb', 9, fileobj=out)
        gzout.write(response)
        gzout.close()
        response = out.getvalue()
        print ('gzipping took', (time.time() - start))
    print '>> Returning %d bytes for %s' % (len(response), url)
    return wsgi_ok(start_response, response, headers)

def main():
    eventlet.wsgi.server(eventlet.listen(('localhost', 8199)), wsgi_proxy,
            log_x_forwarded_for=True, keepalive=False,
            max_size=1024)
    return 0

if __name__ == '__main__':
    exit(main())
