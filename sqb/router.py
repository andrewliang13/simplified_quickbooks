"""Tiny route registry shared by all API modules."""
import re

ROUTES = []


def route(method, pattern):
    regex = re.compile('^' + re.sub(r'<(\w+)>', r'(?P<\1>\\d+)', pattern) + '$')

    def deco(fn):
        ROUTES.append((method, regex, fn))
        return fn
    return deco


class Ctx:
    """Request context handed to every handler."""

    def __init__(self, conn, params, query, body):
        self.conn = conn
        self.p = params      # path params (ints)
        self.q = query       # query string (str -> str)
        self.body = body     # parsed JSON body (dict)
