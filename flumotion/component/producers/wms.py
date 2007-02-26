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

from flumotion.component import feedcomponent
from flumotion.common import log

from flumotion.component.producers import asfparse

class DigestAuth(log.Loggable):
    logCategory = "digestauth"

    timeout = 60*60*3  # 3 hours.
    _qop_type = 'auth' # Others not implemented
    _algorithm = 'MD5' # May also be set to 'MD5-sess'

    def __init__(self, realm):
        self._outstanding = {} # opaque -> (nonce, timestamp)
        self._pushIds = {} # pushid -> authenticated
        self._realm = realm
        self._users = {}

    def addUser(self, username, password):
        self._users[username] = password

    def _cleanupOutstanding(self):
        now = time.time()
        for (opaque, (nonce,ts)) in self._outstanding.items():
            if now - ts > self.timeout:
                del self._outstanding[opaque]

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

    def generateWWWAuthenticateHeader(self, request, stale=False):
        res = 'Digest'
        while True:
            opaque = self._generateOpaque()
            timestamp = time.time()
            if opaque not in self._outstanding:
                nonce = self._generateNonce()
                self._outstanding[opaque] = (nonce, timestamp)
                self.debug("Set opaque '%s' -> nonce '%s'", opaque, nonce)
                break

        params = {'realm':  (True, self._realm),
                  'qop':    (False, self._qop_type),
                  'nonce':  (True, nonce),
                  'opaque': (True, opaque),
                  # We don't send algorithm, if we do WME fails (WTF?)
                  #'algorithm': (False, self._algorithm)
                 }
        if stale:
            params['stale'] = (False, 'true')

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

    def authenticate(self, request, pushId):
        """
        Attempt to authenticate a request.
        Returns an HTTP response code (which should be set on the response)
        Handles HTTP Digest authentication, plus some special handling of the
        pushid cookie that windows-media-encoder uses
        """
        if pushId in self._pushIds:
            # The encoder sends the actual stream without authenticating.
            # We permit it to do this once, with the pushId we issued.
            if self._pushIds[pushId]:
                del self._pushIds[pushId]
                return (http.OK, pushId, False)
        else:
            pushId = random.randint(1, (1<<31)-1)
            self._pushIds[pushId] = False

        if not request.hasHeader("Authorization"):
            self.debug("No auth header, sending unauthorized")
            return (http.UNAUTHORIZED, pushId, False)

        self.debug("Has auth header, parsing")

        authHeader = request.getHeader("Authorization")
        type, attrs = self._parseAuthHeader(authHeader)
        if type.lower() != 'digest':
            self.debug("Not digest auth, bad request")
            return (http.BAD_REQUEST, pushId, False)

        self.debug("Received attributes: %r", attrs)
        required = ['username', 'realm', 'nonce', 'opaque', 'uri', 'response']
        for r in required:
            if r not in attrs:
                self.debug("Required attribute %s is missing", r)
                return (http.BAD_REQUEST, pushId, False)

        # 'qop' is optional, if sent then cnonce and nc are required.
        qop = False
        if 'qop' in attrs:
            if attrs['qop'] != 'auth' or 'cnonce' not in attrs or \
                    'nc' not in attrs:
                self.debug("qop is not auth or cnonce missing or nc missing")
                return (http.BAD_REQUEST, pushId, False)
            qop = True
            nccount = attrs['nc']
            cnonce = attrs['cnonce']
        else:
            # This is also required for md5-sess
            if self._algorithm == 'md5-sess':
                if 'cnonce' not in attrs:
                    self.debug("cnonce not present when md5-sess in use")
                    return (http.BAD_REQUEST, pushId, False)
            nccount = None
            cnonce = None
            
        # WM Encoder sends realm="", so this doesn't match. So, skip this check
        #if attrs['realm'] != self._realm:
        #    request.setResponseCode(http.BAD_REQUEST)
        #    return False

        opaque = attrs['opaque']
        nonce = attrs['nonce']
        username = attrs['username']
        uri = attrs['uri']
        realm = attrs['realm']

        if username not in self._users:
            self.debug("Username not found in users db")
            return (http.UNAUTHORIZED, pushId, False)

        password = self._users[username]

        # Ensure we don't have old ones lying around. Rather inefficient but
        # not a practical problems
        self._cleanupOutstanding()
        if opaque not in self._outstanding:
            self.debug("opaque not in outstanding")
            return (http.UNAUTHORIZED, pushId, True)
        (expectednonce, ts) = self._outstanding[opaque]

        if expectednonce != nonce:
            self.debug("nonce doesn't correspond to opaque")
            return (http.BAD_REQUEST, pushId, False)

        expected = self._calculateRequestDigest(realm, nonce, qop, nccount, 
            cnonce, username, password, request.method, uri)
        response = attrs['response']

        self.debug("Computed expected digest %s, received %s", expected, 
            response)
        if response != expected:
            self.debug("Password incorrect")
            return (http.UNAUTHORIZED, pushId, False)

        # Success!
        self._pushIds[pushId] = True
        return (http.OK, pushId, False)

class WMSRequest(server.Request, log.Loggable):

    def __init__(self, *args, **kw):
        server.Request.__init__(self, *args, **kw)

        self._streaming = False
        self._srcelement = None

    def hasHeader(self, header):
        return header.lower() in self.received_headers

    def setStreaming(self):
        self._streaming = True

    def finish(self):
        if not self._streaming:
            server.Request.finish(self)

    def process(self):
        # TODO: This nasty hack is needed because setHeader() mangles
        # case, which WMEncoder doesn't cope with
        class HackString(str):
            def capitalize(self):
                return self

        digester = self.channel.wmsfactory.digester
        # Pretend to be Windows Media Server, otherwise WME won't connect.
        # Also append a note that we're actually flumotion.
        self.setHeader("Server", "Cougar/9.01.01.3814 "
            "(Flumotion Streaming Server)")
        self.setHeader("Date", http.datetimeToString())
        # Not sure how neccesary these are, but WMS sends them, so why not?
        self.setHeader("Supported", 
            "com.microsoft.wm.srvppair, com.microsoft.wm.sswitch, " \
            "com.microsoft.wm.predstrm, com.microsoft.wm.fastcache, " \
            "com.microsoft.wm.startupprofile")
        self.setHeader(HackString("Content-Length"), 0)
        self.setHeader("Pragma", "no-cache,timeout=60000")

        pushId = 0
        if self.hasHeader("Cookie"):
            cookieKV = self.getHeader("Cookie")
            cookieKey, cookieVal = cookieKV.split('=')
            if cookieKey == 'push-id':
                pushId = int(cookieVal)

        (code, pushId, stale) = digester.authenticate(self, pushId)

        if code >= 400:
            self.debug("Authentication failed")
            self.setResponseCode(code)
            if code == 401:
                self.headers[HackString("WWW-Authenticate")] = \
                    digester.generateWWWAuthenticateHeader(self, stale)
                if pushId:
                    self.headers[HackString("Set-Cookie")] = \
                        "push-id=%d" % pushId
            self.finish()
            return

        ctype = self.getHeader("content-type")
        if ctype == 'application/x-wms-pushsetup':
            if pushId:
                self.headers["Set-Cookie"] = "push-id=%d" % pushId
            self.setResponseCode(http.NO_CONTENT)
            self.finish()
            return
        elif ctype == 'application/x-wms-pushstart':
            self.debug("Got pushstart!")
            if self.channel.wmsfactory.srcelement.isStreaming():
                self.warning("Already streaming")
                self.finish()
                return
            self._srcelement = self.channel.wmsfactory.srcelement
            self._srcelement.setStreaming(True)
            self.finish()
            return
        else:
            self.debug("Unknown content-type: %s", ctype)
            self.finish()
            return

    def dataReceived(self, data):
        if not self._srcelement:
            self.warning("Receiving streaming data without a pushstart request")
            return

        self._srcelement.dataReceived(data)

class WMSChannel(http.HTTPChannel, log.Loggable):

    def __init__(self):
        http.HTTPChannel.__init__(self)

        self._streaming_request = None

    def rawDataReceived(self, data):
        # Windows Media Encoder sends this content-length. Use this as a trigger
        # to switch to our streaming-POST interface
        if self.length == 2147483647 or self._streaming_request:
            if not self._streaming_request:
                self.debug("Got max-length request, switching to streaming POST")
                self._streaming_request = self.requests[-1]
                self._streaming_request.setStreaming()
                self.allContentReceived()
            self.debug("Data received for streaming post request")
            self._streaming_request.dataReceived(data)
        else:
            self.debug("Raw data received for non-streaming request")
            http.HTTPChannel.rawDataReceived(self, data)

    def connectionLost(self, reason):
        self.wmsfactory.srcelement.setStreaming(False)
        return http.HTTPChannel.connectionLost(self, reason)

class WMSFactory(http.HTTPFactory):
    protocol = WMSChannel
    requestFactory = WMSRequest

    def __init__(self, auth, srcelement):
        http.HTTPFactory.__init__(self)

        self.digester = auth
        self.srcelement = srcelement

    def buildProtocol(self, addr):
        channel = http.HTTPFactory.buildProtocol(self, addr)
        channel.requestFactory = self.requestFactory
        channel.wmsfactory = self
        return channel

class WindowsMediaServer(feedcomponent.ParseLaunchComponent):
    """
    A component to act (to a Windows Media Encoder client in push mode) like
    a Windows Media Server, in order to accept an ASF stream.
    """

    def init(self):
        self._srcelement = asfparse.ASFSrc("asfsrc")

    def do_start(self, *args, **kwargs):
        # TODO: Write a real component!
        digester = DigestAuth("Flumotion Streaming Server WMS Component")
        digester.addUser("user", "test3")
        reactor.listenTCP(8888, WMSFactory(digester, self._srcelement))

        return feedcomponent.ParseLaunchComponent.do_start(self, *args, 
            **kwargs)

    def get_pipeline_string(self, properties):
        return "identity name=identity"

    def configure_pipeline(self, pipeline, properties):
        pipeline.add(self._srcelement)

        src = pipeline.get_by_name("identity")

        srcpad = self._srcelement.get_pad("src")
        sinkpad = src.get_pad("sink")

        srcpad.link(sinkpad)

