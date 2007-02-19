# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2007 Fluendo, S.L. (www.fluendo.com).
# All rights reserved.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

import gst
import time
import md5
import random

from twisted.internet import reactor, defer
from twisted.web import resource, server, http

from flumotion.component import component
from flumotion.common import log

class DigestAuth(log.Loggable):
    logCategory = "digestauth"

    timeout = 15 # not used currently.
    _qop_type = 'auth' # Others not implemented
    _algorithm = 'MD5' # May also be set to 'MD5-sess'

    def __init__(self, realm):
        # TODO: at some point we need to explicitly expire things from here
        self._outstanding = {} # opaque -> (nonce, timestamp)
        self._realm = realm
        self._users = {}

    def addUser(self, username, password):
        self._users[username] = password

    # Taken from porter.py; replace later.
    def _generateRandomString(self, numchars):
        """
        Generate a random US-ASCII string of length numchars
        """
        str = ""
        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        for _ in range(numchars):
            str += chars[random.randint(0, len(chars)-1)]

        return str

    def _generateOpaque(self):
        return self._generateRandomString(16)
        
    def _generateNonce(self):
        return self._generateRandomString(16)

    def _generateWWWAuthenticateHeader(self, request, stale=False):
        res = 'Digest'
        while True:
            opaque = self._generateOpaque()
            timestamp = time.time()
            if opaque not in self._outstanding:
                nonce = self._generateNonce()
                self._outstanding[opaque] = (nonce, timestamp)
                break

        params = {'realm':  (True, self._realm),
                  'qop':    (False, self._qop_type),
                  'nonce':  (True, nonce),
                  'opaque': (True, opaque),
                  #'algorithm': (False, self._algorithm)
                 }
        if stale:
            params['stale'] = (False, 'true')

        # TODO: add 'domain', maybe 'algorithm'? Figure out
        # auth vs. auth-int.

        for (k,(quoted,v)) in params.items():
            if quoted:
                res += " %s=\"%s\"" % (k,v)
            else:
                res += " %s=%s" % (k,v)

        return res

    def _parseAuthHeader(self, authHeader):
        try:
            type, data = authHeader.split(" ", 1)
            attrs = {}
            # TODO: rewrite, this doesn't account for various things in the 
            # quoted strings
            def unquote(v):
                if v[0] == '"' and v[-1] == '"':
                    return v[1:-1]
                return v

            for s in data.split(','):
                k,v = s.split('=',1)
                attrs[k.strip()] = unquote(v.strip())

            return type, attrs
        except ValueError:
            return ("", {})

    def _calculateHA1(self, username, realm, password, nonce, cnonce):
        """
        Calculate H(A1) as from specification (RFC2617) section 3.2.2
        """
        m = md5.md5()
        m.update(username)
        m.update(':')
        m.update(realm)
        m.update(':')
        m.update(password)
        HA1 = m.digest()
        if self._algorithm == 'MD5':
            return HA1.encode('hex')
        elif self._algorithm == 'MD5-sess':
            m = md5.md5()
            m.update(HA1)
            m.update(':')
            m.update(nonce)
            m.update(':')
            m.update(cnonce)
            return m.digest().encode('hex')
        else:
            raise NotImplementedError("Unimplemented algorithm")

    def _calculateHA2(self, method, uri):
        # We don't support auth-int, otherwise we'd optionally need to do
        # some more work here
        m = md5.md5()
        m.update(method)
        m.update(':')
        m.update(uri)
        return m.digest().encode('hex')

    def _calculateRequestDigest(self, realm, nonce, qop, ncvalue, cnonce,
            username, password, method, uri):
        HA1 = self._calculateHA1(username, realm, password, nonce, cnonce)
        HA2 = self._calculateHA2(method, uri)

        m = md5.md5()
        m.update(HA1)
        m.update(':')
        m.update(nonce)
        if qop:
            m.update(':')
            m.update(ncvalue)
            m.update(':')
            m.update(cnonce)
            m.update(':')
            m.update(self._qop_type)
        m.update(':')
        m.update(HA2)

        return m.digest().encode('hex')

    def authenticate(self, request):
        if not hasHeader(request, "Authorization"):
            #request.setHeader("WWW-Authenticate", 
            #    self._generateWWWAuthenticateHeader(request))
            request.headers["WWW-Authenticate"] = \
                self._generateWWWAuthenticateHeader(request)
            request.setResponseCode(http.UNAUTHORIZED)
            self.debug("No auth header, sending unauthorized")
            return False

        self.debug("Has auth header, parsing")

        authHeader = request.getHeader("Authorization")
        type, attrs = self._parseAuthHeader(authHeader)
        if type.lower() != 'digest':
            self.debug("Not digest auth, bad request")
            request.setResponseCode(http.BAD_REQUEST)
            return False

        self.debug("Received attributes: %r", attrs)
        required = ['username', 'realm', 'nonce', 'opaque', 'uri', 'response']
#                    'algorithm']
        for r in required:
            if r not in attrs:
                self.debug("Required attribute %s is missing", r)
                request.setResponseCode(http.BAD_REQUEST)
                return False
        # 'qop' is optional, if sent then cnonce and nc-value are required.
        qop = False
        if 'qop' in attrs:
            if attrs['qop'] != 'auth' or 'cnonce' not in attrs or \
                    'nc' not in attrs:
                self.debug("qop is not auth or cnonce missing or nc missing")
                request.setResponseCode(http.BAD_REQUEST)
                return False
            qop = True
            nccount = attrs['nc']
            cnonce = attrs['cnonce']
        else:
            # This is also required for md5-sess
            if self._algorithm == 'md5-sess':
                if 'cnonce' not in attrs:
                    self.debug("qop not set, but using md5-sess, so cnonce is "
                        "required, but is missing")
                    request.setResponseCode(http.BAD_REQUEST)
                    return False
            nccount = None
            cnonce = None
            
        # WM Encoder sends realm="", so this doesn't match. So, ignore it.
        # TODO: Figure out whether it computes the response using this realm
        # (the empty string) or the actual realm.
        #if attrs['realm'] != self._realm:
        #    request.setResponseCode(http.BAD_REQUEST)
        #    return False

        #if attrs['algorithm'] != self._algorithm:
        #    request.setResponseCode(http.BAD_REQUEST)
        #    return False

        opaque = attrs['opaque']
        nonce = attrs['nonce']
        username = attrs['username']
        uri = attrs['uri']
        realm = attrs['realm']

        # TODO: check that uri is the same as the request URI.

        if username not in self._users:
            self.debug("Username not found in users db")
            request.setResponseCode(http.UNAUTHORIZED)
            request.headers["WWW-Authenticate"] = \
                self._generateWWWAuthenticateHeader(request)
            return False

        password = self._users[username]

        if opaque not in self._outstanding:
            self.debug("opaque not in outstanding")
            request.setResponseCode(http.UNAUTHORIZED)
            request.headers["WWW-Authenticate"] = \
                self._generateWWWAuthenticateHeader(request)
            return False
        (expectednonce, ts) = self._outstanding[opaque]
        if expectednonce != nonce:
            self.debug("nonce doesn't correspond to opaque")
            request.setResponseCode(http.BAD_REQUEST)
            return False
        # TODO: expire entries based on ts?

        expected = self._calculateRequestDigest(realm, nonce, qop, nccount, 
            cnonce, username, password, request.method, uri)
        response = attrs['response']

        self.debug("Computed expexted digest %s, received %s", expected, response)
        if response != expected:
            self.debug("Bad password")
            request.setResponseCode(http.UNAUTHORIZED)
            request.headers["WWW-Authenticate"] = \
                self._generateWWWAuthenticateHeader(request)
            return False

        # Success!
        return True

def hasHeader(request, header):
    return header.lower() in request.getAllHeaders()
        
class TestResource(resource.Resource, log.Loggable):

    def __init__(self, realm):
        resource.Resource.__init__(self)
        self.isLeaf = True
        self._digester = DigestAuth(realm)
        self._digester.addUser("user", "test")

    def render_POST(self, request):
        # TODO: ok,let's ignore authentication for the moment...
        #if not hasattr(request.channel, "_hack_authenticated"):
        if True:
            self.debug("Trying authentication")
            if not self._digester.authenticate(request):
                request.setHeader("Supported", 
                    "com.microsoft.wm.srvppair, com.microsoft.wm.sswitch, " \
                    "com.microsoft.wm.predstrm, com.microsoft.wm.fastcache, " \
                    "com.microsoft.wm.startupprofile")
                request.setHeader("Server", "Cougar/9.01.01.3814")
                return ""
        #request.channel._hack_authenticated = True

        pushId = 0
        if hasHeader(request, "Cookie"):
            cookieKV = request.getHeader("Cookie")
            cookieKey, cookieVal = cookieKV.split('=')
            if cookieKey == 'push-id':
                pushId = int(cookieVal)
        if not pushId:
            pushId = 213792184 # Some random number. TODO!

        ctype = request.getHeader("content-type")
        if ctype == 'application/x-wms-pushsetup':
            if pushId:
                request.headers["Set-Cookie"] = "push-id=%d" % pushId
            request.setHeader("Supported", 
                "com.microsoft.wm.srvppair, com.microsoft.wm.sswitch, " \
                "com.microsoft.wm.predstrm, com.microsoft.wm.fastcache, " \
                "com.microsoft.wm.startupprofile")
            request.setHeader("Server", "Cougar/9.01.01.3814")
            request.setResponseCode(http.NO_CONTENT)
            return ""
        elif ctype == 'application/x-wms-pushstart':
            self.debug("Got pushstart!")
            request.content = open("/tmp/out.dump", "w")
            return ""
        else:
            self.debug("Unknown content-type: %s", ctype)
            return ""

class WindowsMediaServer(component.BaseComponent):
    """
    A component to act (to a Windows Media Encoder client in push mode) like
    a Windows Media Server, in order to accept an ASF stream.
    """

    def do_start(self, *args, **kwargs):
        resource = TestResource("Flumotion Streaming Server WMS Component")
        reactor.listenTCP(8888, server.Site(resource=resource))

        return defer.succeed(None)
