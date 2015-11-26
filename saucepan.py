# !/usr/bin/env python
# -*- coding: utf-8 -*-
#

import httplib
from collections import OrderedDict
from abc import abstractmethod
import re
import logging

__author__ = 'KeiDii'
__version__ = '0.3'
__license__ = 'MIT'

DEFAULT_LISTEN_HOST = '0.0.0.0'
DEFAULT_LISTEN_PORT = 8008

HTTP_CODES = httplib.responses.copy()
HTTP_CODES[418] = "I'm a teapot"  # RFC 2324
HTTP_CODES[428] = "Precondition Required"
HTTP_CODES[429] = "Too Many Requests"
HTTP_CODES[431] = "Request Header Fields Too Large"

HTTP_CODE_RANGES = {1: 'Continue', 2: 'Success', 3: 'Redirect', 4: 'Request Error', 5: 'Server Error'}


def _re_get_args_kwargs(exp, mo):
  idx = exp.groupindex.values()
  groups = mo.groups()
  kwargs = mo.groupdict()
  args = []
  for i in range(len(groups)):
    if (i + 1) not in idx:  # not a groupdict
      args.append(groups[i])
  return args, kwargs


def get_default_http_message(code):
  c = int(code) / 100
  return HTTP_CODE_RANGES.get(c, None)


def http_status(code, message=None):
  code = int(code)
  if message is None:
    message = HTTP_CODES.get(code, None)
    if message is None:
      message = get_default_http_message(code)
      if message is None:
        message = 'WTF'
  return "{0} {1}".format(code, message)


class GenericServer(object):
  """ Generic, bottle-compatible build-in server """

  def __init__(self, **options):
    self.server = None
    self.options = options

  def run(self, wsgi_app):
    """
    Should Run forever
    :param wsgi_app: wsgi application
    :return:
    """
    pass


class WSGIRefServer(GenericServer):
  """ WSGIRef-based server
  """

  def run(self, wsgi_app):

    host = self.options.get('host', DEFAULT_LISTEN_HOST)
    port = int(self.options.get('port', DEFAULT_LISTEN_PORT))

    import wsgiref.simple_server as ref_srv

    class FixedHandler(ref_srv.WSGIRequestHandler):
      def address_string(self):  # Prevent reverse DNS lookups please.
        return self.client_address[0]

      def log_request(*args, **kw):
        pass

    srv_class = ref_srv.WSGIServer
    hnd_class = FixedHandler
    logging.debug("Starting WSGIRef simple server on {0}:{1}".format(host, port))
    self.server = ref_srv.make_server(host, port, wsgi_app, srv_class, hnd_class)
    try:
      self.server.serve_forever()
    except KeyboardInterrupt:
      self.server.server_close()


class LastUpdatedOrderedDict(OrderedDict):
  def __setitem__(self, key, value, dict_setitem=dict.__setitem__):
    if key in self:
      del self[key]
    OrderedDict.__setitem__(self, key, value, dict_setitem=dict_setitem)


class CaseInsensitiveHttpEnv(object):
  _extra_keys = ('CONTENT_TYPE', 'CONTENT_LENGTH')
  _env = None

  def __init__(self, env):
    self._env = env
    # for k,v in env.iteritems():
    #  print k,v

  def __getitem__(self, item):
    return self.get(item, None)

  def get(self, item, default=None, require=False):
    item = str(item)
    item = item.upper()
    if item.startswith("HTTP_"):
      pass
    elif item == 'METHOD':
      item = 'REQUEST_METHOD'
    elif item == 'PROTOCOL':
      item = 'SERVER_PROTOCOL'
    else:
      item = "HTTP_" + item
    # print "GET KEY ", item
    val = self._env.get(item, None)
    if val is None:
      if require:
        raise KeyError(item)
      return default
    return val

  def has(self, key):
    return self.get(key) is not None

  def check(self, key, val):
    cur_val = self.get(key, default=None)
    # print "COMPARE ", key, val, cur_val
    if cur_val is None:
      return False
    if isinstance(val, list):
      if cur_val in val:
        return True
    return cur_val == val


class HttpMessage(object):
  body = ''


class HttpRequest(HttpMessage):
  verb = None
  version = None
  _http_proto = None
  _http_version = None
  headers = None

  def __init__(self, env):
    self.headers = CaseInsensitiveHttpEnv(env)
    self.verb = env.get('REQUEST_METHOD')
    self.version = env.get('SERVER_PROTOCOL')
    self.path = env.get('PATH_INFO')
    self.host = env.get('HTTP_HOST')
    self._http_version, self._http_version = self.version.split('/')

  def uri(self, host=False):
    if host:
      return self.host + self.path
    else:
      return self.path


CONTENT_LENGTH_HEADER = 'Content-Length'


class HttpResponse(HttpMessage):
  status_code = 200
  status_message = None
  headers = LastUpdatedOrderedDict()
  fix_content_length = True
  http_version = ''

  def __init__(self, version='HTTP/1.1'):
    self.http_version = version

  def get_status(self):
    return http_status(self.status_code, self.status_message)

  def get_headers(self):  # return Camel-Case headers + values as list[]
    resp = []
    for k, v in self.headers.iteritems():
      resp.append((k.title(), str(v)))
    return resp

  def finish(self):  # calculate content-length header if not set
    if self.fix_content_length:
      s = len(self.body)
      self.headers[CONTENT_LENGTH_HEADER] = str(s)


class TheContext(object):
  def __init__(self, env):
    self.request = HttpRequest(env)
    self.response = HttpResponse(version=self.request.version)
    self.env = env


class AbstractRouter(object):
  _routes = []
  default = None

  def __init__(self):
    self.setup()

  @abstractmethod
  def setup(self):
    pass

  def _pre_process(self, kw):
    return kw

  def _default_route(self, ctx, **kw):
    if callable(self.default):
      self.default(ctx, **kw)

  def add_entry(self, testable, **kw):
    logging.debug("Adding new route [testable=%s] "%str(testable))
    kw['testable'] = testable
    self._routes.append(self._pre_process(**kw))
    pass

  @abstractmethod
  def try_route(self, ctx, **route_args):
    pass

  def select_route(self, ctx):
    for rt in self._routes:
      if self.try_route(ctx, **rt):
        return ctx
    logging.warning("No valid route found ! Try default ...")
    self._default_route(ctx)
    return ctx


ROUTE_CHECK_UNDEF = None
ROUTE_CHECK_ALWAYS = 0xff
ROUTE_CHECK_STR = 1
ROUTE_CHECK_SIMPLE = 2
ROUTE_CHECK_REGEX = 3
ROUTE_CHECK_CALL = 4
ROUTE_GENERATOR = 5
DEFAULT_ROUTE_TYPE = ROUTE_CHECK_SIMPLE
ROUTE_SIMPLE_CHAR_SET = 'a-zA-Z0-9'


def method(m):
  return [m]


METHOD_GET = ['GET']
METHOD_POST = ['POST']


def _router_final_call(ctx, fn, a, kw):
  data = fn(ctx, *a, **kw)
  if data:
    ctx.response.body = data


class DefaultRouter(AbstractRouter):
  _SIMPLE_RE_FIND = r'<([^>]*)>'
  _SIMPLE_RE_REPLACE = r'(?P<\1>[' + ROUTE_SIMPLE_CHAR_SET + ']*)'
  _type_mapping = {}

  def setup(self):
    # TODO : rewrite dict routes to classes ? (need to benchmark !)
    self._type_mapping = {
      ROUTE_CHECK_ALWAYS: self._test_always,
      ROUTE_CHECK_STR: self._test_str,
      ROUTE_CHECK_SIMPLE: self._test_re,
      ROUTE_CHECK_REGEX: self._test_re,
      ROUTE_CHECK_CALL: self._test_call,
      ROUTE_GENERATOR: self._test_generator,
    }

  def _test_always(self, ctx, testable=None, target=None, **kw):
    _router_final_call(ctx, target, [], kw)
    return True

  def _test_str(self, ctx, testable='', target=None, **ex):
    uri = ctx.request.uri()
    if uri == testable:
      _router_final_call(ctx, target, [], ex)
      return True
    return False

  def _test_re(self, ctx, testable=None, target=None, _re=None, **ex):
    uri = ctx.request.uri()
    mo = _re.match(uri)
    if not mo:
      return False
    args, kwargs = _re_get_args_kwargs(_re, mo)
    # print " --> ", args, kwargs
    ex.update(kwargs)
    _router_final_call(ctx, target, args, ex)
    return True

  def _test_call(self, ctx, testable=None, target=None, **ex):
    ret_val = testable(ctx, **ex)
    args = []
    if isinstance(ret_val, tuple) or isinstance(ret_val, list):
      bool_val = ret_val[0]
      args = ret_val[1:]
    else:
      bool_val = ret_val
    if bool_val:
      _router_final_call(ctx, target, args, route)
      return True
    return False

  def _test_generator(self, ctx, testable=None, target=None, **ex):
    ret_val = testable(ctx, **ex)
    if ret_val is None:
      return False
    args = []
    if isinstance(ret_val, tuple) or isinstance(ret_val, list):
      func = ret_val[0]
      args = ret_val[1:]
    else:
      func = ret_val
    _router_final_call(ctx, func, args, route)
    return True

  def _pre_process(self, **kw):  # TODO : this could return object with proper methods/values/etc
    testable = kw.get('testable')  # <- required argument
    target = kw.get('target', None)  # <- ref to func || None
    route_type = kw.get('route_type', ROUTE_CHECK_UNDEF)
    if 'headers' not in kw:
      kw['headers'] = []
    else:
      if not isinstance(kw['headers'], list):
        kw['headers'] = []

    # convert al check_%s key into required header names
    for key in kw.keys():
      if key.startswith("check_"):
        item = key.split("_", 1)[1]
        kw['headers'].append((item, kw[key]))
        del kw[key]

    if route_type == ROUTE_CHECK_UNDEF:
      logging.debug("Route type is not set. Guessing ...")
      if testable is None:
        route_type = ROUTE_CHECK_ALWAYS
      elif isinstance(testable, basestring):
        if "<" in testable:
          route_type = ROUTE_CHECK_SIMPLE
        else:
          route_type = ROUTE_CHECK_STR
      if callable(testable):  # callable can be check or generator
        if target is None:
          route_type = ROUTE_GENERATOR
        else:
          route_type = ROUTE_CHECK_CALL
      kw['route_type'] = route_type
      logging.debug("Route type after guess: %s"%route_type)
    else:
      # "* Route type already set to :", route_type
      pass

    # setup proxy function to perform test.
    # Setting this here allow to skip another switch-case construct in try_route
    kw['_callable'] = self._type_mapping.get(route_type, None)
    if route_type == ROUTE_CHECK_REGEX:
      kw['_re'] = re.compile(testable)
    if route_type == ROUTE_CHECK_SIMPLE:
      _tmp = re.sub(self._SIMPLE_RE_FIND, self._SIMPLE_RE_REPLACE, testable)
      kw['_re'] = re.compile(_tmp)
    return kw

  def try_route(self, ctx, _callable=None, headers=None, route_type=None, **args):
    # print 'Trying ... ', args
    if headers and len(headers) > 0:
      for key, val in headers:
        if not ctx.request.headers.check(key, val):
          return False
    if _callable and callable(_callable):
      return _callable(ctx, **args)
    else:
      log.error("Ouch! problem with _callable !")


DEFAULT_HEADERS = {
  'Content-type': 'text/html',
  'Server': 'Saucepan (%s)' % __version__,
}

def _silent_error_handler(ctx, error):
  ctx.response.headers['Content-type'] = 'text/html'
  return "500: server fail !"

def _verbose_error_handler(ctx, error):
  import traceback
  import sys

  info = sys.exc_info()
  traceback.print_exception(*info)
  body = "SERVER FAIL:<br><pre>\n"
  body += '\n'.join(traceback.format_exception(*info))
  body += "\n\n</pre>"
  ctx.response.headers['Content-type'] = 'text/html'
  return body

def _default_request_handler(ctx):
  ctx.response.status_code = 404
  return "Not found!"

class CookingPot(object):
  _write_using_writer = False
  router = DefaultRouter()
  _be_verbose = True
  _exception_handlers = []

  def __init__(self, router_class=None):
    logging.debug("Main object init")
    if router_class:
      self.router = router_class()
    self.router.default = _default_request_handler

  def handle_exception(self, ex_type, **kw):
    def _wrapper(f):
      self.add_exception_handler(ex_type, f, **kw)

    return _wrapper

  def add_exception_handler(self, ex_type, fn, **kw):
    self._exception_handlers.append(
      dict(
        ex_type=ex_type,
        handler=fn,
        kwargs=kw,
      )
    )

  def handle_error(self, ctx, error):
    ctx.response.status_code = 500
    ctx.response.status_message = "Server Fail"
    for entry in self._exception_handlers:
      if isinstance(error, entry['ex_type']):
        return entry['handler'](ctx, error, **entry['kwargs'])
    if self._be_verbose:
      return _verbose_error_handler(ctx, error)
    else:
      return _silent_error_handler(ctx, error)

  def route(self, testable, **kw):
    def _wrapper(f):
      # print " ** wrapped : ", f, " ** "
      self.add_route(testable, target=f, **kw)

    return _wrapper

  def add_route(self, testable, target=None, **kw):
    self.router.add_entry(testable, target=target, **kw)

  def wsgi_handler(self, environ, start_response):
    logging.debug('WSGI handler called ...')
    exc_info = None
    ctx = TheContext(environ)
    for k, v in DEFAULT_HEADERS.iteritems():
      ctx.response.headers[k] = v
    try:
      self.router.select_route(ctx)
      ctx.response.finish()
    except Exception as ex:
      ctx.response.body = self.handle_error(ctx, ex)
    body = ctx.response.body
    headers = ctx.response.get_headers()
    status = ctx.response.get_status()
    body_writer = start_response(status, headers, exc_info)
    if self._write_using_writer:
      if callable(body_writer):
        body_writer(body)
        return ''
      else:
        return body
    else:
      return body


pan = CookingPot()

# expose in globals, so we can use @decorator
route = pan.route


def _wsgi_handler(environ, start_response):
  return pan.wsgi_handler(environ, start_response)


def wsgi_interface():
  return _wsgi_handler

# expose WSGI handler
application = wsgi_interface()


def run(server_class=None, **opts):
  logging.debug("Preparing WSGI server ... ")
  if server_class is None:
    server_class = WSGIRefServer
  handle = server_class(**opts)
  logging.debug("Running server ... ")
  handle.run(_wsgi_handler)


if __name__ == '__main__':
  logging.warn("Running stanalone ?")
  run()

# end
