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
import random

from twisted.internet import reactor, defer, error
from twisted.web import resource, server, http
from twisted.cred import credentials

from flumotion.component import feedcomponent
from flumotion.common import log, errors, messages, keycards

from flumotion.twisted import fdserver
from flumotion.component.misc.porter import porterclient
from flumotion.component.component import moods

from flumotion.component.producers import asfparse

from flumotion.common.messages import N_
T_ = messages.gettexter('flumotion-windowsmedia')

class DigestAuth(log.Loggable):
    logCategory = "digestauth"

    timeout = 60*60*3  # 3 hours.
    _qop_type = 'auth' # Others not implemented
    _algorithm = "MD5"

    def __init__(self, component):
        self._outstanding = {} # opaque -> (nonce, timestamp)
        self._pushIds = {} # pushid -> authenticated

        self._realm = None
        self._component = component
        self._bouncerName = None
        self._requesterId = component.getName()
        self._ignoreNonceAndOpaque = False

    def setBouncerName(self, bouncerName):
        self._bouncerName = bouncerName

    def setRealm(self, realm):
        self._realm = realm

    def enableReplayAttacks(self):
        self._ignoreNonceAndOpaque = True

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
        res = 'Digest '
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
                  'algorithm': (False, self._algorithm)
                 }
        if stale:
            params['stale'] = (False, 'true')

        first = True
        for (k,(quoted,v)) in params.items():
            if not first:
                res += ","
            else:
                first = False

            if quoted:
                res += "%s=\"%s\"" % (k,v)
            else:
                res += "%s=%s" % (k,v)

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

    def authenticate(self, request, pushId):
        """
        Attempt to authenticate a request.
        Returns a tuple of (code, pushId, stale), or a deferred which will
        return that.
        'code' is the HTTP response code which should be set on the response.

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
            #if self._algorithm == 'md5-sess':
            #    if 'cnonce' not in attrs:
            #        self.debug("cnonce not present when md5-sess in use")
            #        return (http.BAD_REQUEST, pushId, False)
            nccount = None
            cnonce = None
            
        if attrs['realm'] != self._realm:
            request.setResponseCode(http.BAD_REQUEST)
            return False

        opaque = attrs['opaque']
        nonce = attrs['nonce']
        username = attrs['username']
        uri = attrs['uri']
        #realm = attrs['realm']

        # Ensure we don't have old ones lying around. Rather inefficient but
        # not a practical problems
        self._cleanupOutstanding()
        if not self._ignoreNonceAndOpaque:
            if opaque not in self._outstanding:
                self.debug("opaque not in outstanding")
                # WME ignores 'stale', unfortunately...
                return (http.UNAUTHORIZED, pushId, True)
            (expectednonce, ts) = self._outstanding[opaque]

            if expectednonce != nonce:
                self.debug("nonce doesn't correspond to opaque")
                return (http.BAD_REQUEST, pushId, False)

        response = attrs['response']

        keycard = keycards.HTTPDigestKeycard(username)
        keycard.nonce = nonce
        keycard.response = response

        keycard.method = request.method
        keycard.uri = uri

        if qop:
            keycard.qop = self._qop_type 
            keycard.cnonce = cnonce
            keycard.ncvalue = nccount

        keycard.requesterId = self._requesterId

        self.debug("Authenticating keycard against bouncer %s", 
            self._bouncerName)
        d = self._component.medium.callRemote('authenticate', 
            self._bouncerName, keycard)

        def _success(result):
            self.debug("Got result %r, keycard now %r", result, keycard)
            if result and result.state == keycards.AUTHENTICATED:
                self._pushIds[pushId] = True
                return (http.OK, pushId, False)
            else:
                return (http.UNAUTHORIZED, pushId, False)

        def _failed(failure):
            self.debug("Authentication failed: %r", failure)
            return (http.UNAUTHORIZED, pushId, False)

        d.addCallbacks(_success, _failed)
        return d

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
        # This nasty hack is needed because setHeader() mangles case, which 
        # WMEncoder doesn't cope with. Is there a better way to do this?
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

        # Handle the rest of this as deferred crap
        d = defer.maybeDeferred(digester.authenticate, self, pushId)
        def authenticated((code, pushId, stale)):
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

            self.debug("Authentication successful: code %d, pushId %d", 
                code, pushId)

            ctype = self.getHeader("content-type")
            if ctype == 'application/x-wms-pushsetup':
                if pushId:
                    self.headers["Set-Cookie"] = "push-id=%d" % pushId
                self.setResponseCode(http.NO_CONTENT)
                self.finish()
                return
            elif ctype == 'application/x-wms-pushstart':
                self.debug("Got pushstart!")
                if self.channel.wmsfactory.streamingRequest:
                    self.warning("Already streaming; dropping existing "
                        "connection")
                    self.channel.wmsfactory.streamingRequest.finish()
                    self.channel.wmsfactory.srcelement.resetASFParser()

                self.channel.wmsfactory.streamingRequest = self
                self._srcelement = self.channel.wmsfactory.srcelement
                self.finish()
                return
            else:
                self.debug("Unknown content-type: %s", ctype)
                self.finish()
                return

        d.addCallback(authenticated)
        return d

    def dataReceived(self, data):
        if not self._srcelement:
            self.warning("Receiving streaming data without a pushstart request")
            return

        self._srcelement.dataReceived(data)

class WMSChannel(http.HTTPChannel, log.Loggable):

    def __init__(self):
        http.HTTPChannel.__init__(self)

        self._streaming_request = None
        self.wmsfactory = None

    def rawDataReceived(self, data):
        # Windows Media Encoder sends this content-length. Use this as a trigger
        # to switch to our streaming-POST interface
        if self.length == 2147483647 or self._streaming_request:
            if not self._streaming_request:
                self.debug("Got max-length request, switching to streaming POST")
                self._streaming_request = self.requests[-1]
                self._streaming_request.setStreaming()
                self.allContentReceived()
            self.log("Data received for streaming post request")
            self._streaming_request.dataReceived(data)
        else:
            self.log("Raw data received for non-streaming request")
            http.HTTPChannel.rawDataReceived(self, data)

class WMSFactory(http.HTTPFactory):
    protocol = WMSChannel
    requestFactory = WMSRequest

    def __init__(self, auth, srcelement):
        http.HTTPFactory.__init__(self)

        self.digester = auth
        self.srcelement = srcelement

        self.streamingRequest = None

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

    def do_check(self):
        props = self.config['properties']

        if props.get('type', 'master') == 'slave':
            for k in 'socket-path', 'username', 'password':
                if not 'porter-' + k in props:
                    msg = "slave mode, missing required property 'porter-%s'" % k
                    return defer.fail(errors.ConfigError(msg))

    def do_setup(self):
        props = self.config['properties']
        self._authenticator = DigestAuth(self)

        realm = props.get('realm', "Flumotion Windows Media Server Component")
        self._authenticator.setRealm(realm)

        if 'bouncer' in props:
            bouncerName = props['bouncer']
            self._authenticator.setBouncerName(bouncerName)

        if not props.get('secure', True):
            self._authenticator.enableReplayAttacks()

        return feedcomponent.ParseLaunchComponent.do_setup(self)

    def do_start(self, *args, **kwargs):
        factory = WMSFactory(self._authenticator, self._srcelement)
        if self.type == 'slave':
            # Slaved to a porter...
            d1 = feedcomponent.ParseLaunchComponent.do_start(self,
                *args, **kwargs)

            d2 = defer.Deferred()
            mountpoints = [self.mountPoint]
            self._pbclient = porterclient.HTTPPorterClientFactory(
                factory, mountpoints, d2)

            creds = credentials.UsernamePassword(self._porterUsername,
                self._porterPassword)
            self._pbclient.startLogin(creds, self.medium)

            self.debug("Starting porter login at \"%s\"", self._porterPath)
            # This will eventually cause d2 to fire
            reactor.connectWith(
                fdserver.FDConnector, self._porterPath,
                self._pbclient, 10, checkPID=False)

            return defer.DeferredList([d1, d2])
        else:
            # Streamer is standalone.
            try:
                self.debug('Listening on %d' % self.port)
                reactor.listenTCP(self.port, factory)
                return feedcomponent.ParseLaunchComponent.do_start(self, *args,
                    **kwargs)
            except error.CannotListenError:
                t = 'Port %d is not available.' % self.port
                self.warning(t)
                m = messages.Error(T_(N_(
                    "Network error: TCP port %d is not available."), self.port))
                self.addMessage(m)
                self.setMood(moods.sad)
                return defer.fail(errors.ComponentStartHandledError(t))
            
        reactor.listenTCP(8888, factory)

        return feedcomponent.ParseLaunchComponent.do_start(self, *args, 
            **kwargs)

    def do_stop(self):
        if self.type == 'slave' and self._pbclient:
            d1 = self._pbclient.deregisterPath(self.mountPoint)
            d2 = feedcomponent.ParseLaunchComponent.do_stop(self)
            return defer.DeferredList([d1,d2])
        else:
            return feedcomponent.ParseLaunchComponent.do_stop(self)

    def get_pipeline_string(self, properties):
        # We require an element by name for later adding our actual source 
        # element (which isn't in the registry, so we can't use it here), and
        # because returning an empty string here isn't allowed.
        return "identity name=identity silent=true"

    def configure_pipeline(self, pipeline, properties):
        pipeline.add(self._srcelement)

        src = pipeline.get_by_name("identity")

        srcpad = self._srcelement.get_pad("src")
        sinkpad = src.get_pad("sink")

        srcpad.link(sinkpad)

        self.type = properties.get('type', 'master')
        if self.type == 'slave':
            self._porterPath = properties['porter-socket-path']
            self._porterUsername = properties['porter-username']
            self._porterPassword = properties['porter-password']
        else:
            self.port = int(properties.get('port', 8888))

        self.mountPoint = properties.get('mount-point', '/')
        if not self.mountPoint.startswith('/'):
            self.mountPoint = '/' + self.mountPoint

