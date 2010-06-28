#!/usr/bin/env python
from __future__ import with_statement

import base64
import contextlib
import gzip
import StringIO
import time

import eventlet
eventlet.monkey_patch()
import eventlet.wsgi

from eventlet.green import httplib
from eventlet.green import urllib2

import lxml
import lxml.html
import memcache

PROXIED_HEADERS = ('HTTP_USER_AGENT', 'HTTP_ACCEPT_CHARSET', 'HTTP_ACCEPT',
                'HTTP_ACCEPT_LANGUAGE', )#'HTTP_COOKIE', 'HTTP_ACCEPT_CHARSET')
REDIRECT_CODES = (301, 302, 303,)

CACHE = memcache.Client(('127.0.0.1:11212',))

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


class Munger(object):
    def __init__(self, page_content, **kwargs):
        self.pool = eventlet.GreenPool()
        self.page_content = page_content
        self.doc = lxml.html.document_fromstring(page_content)

    def munge(self):
        for element in self.doc.getiterator():
            method = '_handle_%s' % element.tag
            method = getattr(self, method, None)
            if method is None:
                continue
            self.pool.spawn(method, element)
        self.pool.waitall()
        return lxml.html.tostring(self.doc)

    def _handle_img(self, elem):
        if not elem.attrib.get('src'):
            return elem

        source = elem.attrib['src']
        image = fetch_from('GET', source, {})
        image = image.read()
        b64image = base64.encodestring(image)
        pieces = source.split('.')
        elem.attrib['src'] = 'data:image/%s;base64,%s' % (pieces[-1], b64image)
        return elem

    def _handle_link(self, elem):
        if not elem.attrib.get('href') or not elem.attrib.get('type') == 'text/css':
            return elem

        href = elem.attrib['href']
        css = fetch_from('GET', href, {})
        css = css.read()
        b64css = base64.encodestring(css)
        elem.attrib['href'] = 'data:text/css;base64,%s' % b64css
        return elem

    def _ignore_handle_script(self, elem):
        if not elem.attrib.get('src'):
            return elem

        src = elem.attrib['src']
        js = fetch_from('GET', src, {})
        js = js.read()
        b64js = base64.encodestring(js)
        elem.attrib['src'] = 'data:text/x-js,%s' % b64js
        return elem

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

    cached = False
    #if CACHE.get(url):
    if False:
        print '>>> Getting %s from the cache' % url
        cached = True

    try:
        response = fetch_from(env['REQUEST_METHOD'], url, headers)
    except urllib2.HTTPError, ex:
        start_response('%s %s' % (ex.getcode(), ex.info()), [])
        return ['']

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
    parts = url.split('.')
    suffix = parts[-1]
    if suffix:
        suffix = suffix.split('?')[0]
    munger = None
    if headers.get('content-type') == 'text/html':
        munger = Munger(response)
        response = munger.munge()

    #if not cached and headers.get('cache-control'):
    if False:
        parts = headers['cache-control'].split(',')
        for part in parts:
            part = part.strip()
            if not part.startswith('max-age'):
                continue
            unused, age = part.split('=')
            age = int(age)
            if age <= 0:
                continue
            print ('I should cache %s for %ss (%d bytes)' % (url, age, len(response)))
            CACHE.set(url, response, time=age)

    print ('Sending proxy response for', url)
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
