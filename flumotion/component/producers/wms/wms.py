# -*- Mode: Python; test-case-name: flumotion.test.test_component_wms -*-
# vi:si:et:sw=4:sts=4:ts=4

# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006,2007,2008,2009 Fluendo, S.L.
# Copyright (C) 2010,2011 Flumotion Services, S.A.
# All rights reserved.
#
# This file may be distributed and/or modified under the terms of
# the GNU Lesser General Public License version 2.1 as published by
# the Free Software Foundation.
# This file is distributed without any warranty; without even the implied
# warranty of merchantability or fitness for a particular purpose.
# See "LICENSE.LGPL" in the source distribution for more information.
#
# Headers in this file shall remain intact.

# Note: Since this was written, Microsoft has published documentation of the
# format. See: http://download.microsoft.com/download/9/5/E/95EF66AF-9026-4BB0-A41D-A4F81802D92C/%5BMS-WMHTTP%5D.pdf
# The current implementation is based on reverse-engineering; it should be
# verified against the spec and any issues fixed.

import random
import time

from twisted.cred import credentials
from twisted.internet import reactor, defer, error, protocol
from twisted.protocols import basic
from twisted.web import server, http

from flumotion.common import log, errors, keycards
from flumotion.common.i18n import gettexter, N_
from flumotion.common.messages import Error
from flumotion.component import feedcomponent
from flumotion.component.component import moods
from flumotion.component.misc.porter import porterclient
from flumotion.component.producers.wms import asfparse
from flumotion.twisted import fdserver

T_ = gettexter('flumotion-windowsmedia')


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
        if request.channel._is_authenticated:
            return (http.OK, pushId, False)
        if pushId in self._pushIds:
            # The encoder sends the actual stream without authenticating.
            # We permit it to do this with the pushId we issued.
            if self._pushIds[pushId]:
                request.channel._is_authenticated = True
                return (http.OK, pushId, False)
        else:
            pushId = random.randint(1, (1<<31)-1)
            self._pushIds[pushId] = False

        if not request.hasHeader("Authorization"):
            self.debug("No auth header, sending unauthorized")
            return (http.UNAUTHORIZED, pushId, False)

        self.debug("Has auth header, parsing (%r)",
                   request.getHeader('Authorization'))

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
                # Sometimes WME doesn't send opaque, so try sending 'stale'
                if r == 'opaque':
                    return (http.UNAUTHORIZED, pushId, True)
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

        keycard = keycards.KeycardHTTPDigest(username)
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
        self._bytes_read = 0

    def hasHeader(self, header):
        return header.lower() in self.received_headers

    def process(self):
        # This nasty hack is needed because setHeader() mangles case, which
        # WMEncoder doesn't cope with. Is there a better way to do this?
        class HackString(str):
            def capitalize(self):
                return self

        digester = self.channel.factory.digester
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

        pushId = int(self.getCookie('push-id') or 0)

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
                self.debug('channel "length": %r', self.channel.length)
                disconnect = False
                if self.channel.length > 0:
                    # self.finish() is not enough for twisted to
                    # cleanup persistent channel properly if we didn't
                    # read the whole data contained in the POST's
                    # Content-Length - we need to do it ourselves

                    # right now we just drop connection after writing
                    # the response
                    # FIXME: better handling (keeping the channel
                    # open, etc.) left for when(/if?) it's really needed
                    disconnect = True

                self.finish(disconnect=disconnect)
                return

            self.debug("Authentication successful: code %d, pushId %d",
                code, pushId)

            ctype = self.getHeader("content-type")
            factory = self.channel.factory
            if ctype == 'application/x-wms-pushsetup':
                if factory.streamingRequest:
                    self.warning("Already streaming; dropping existing "
                        "connection")
                    factory.streamingRequest.finish(disconnect=True)

                factory.srcelement.resetASFParser()

                if pushId:
                    self.headers["Set-Cookie"] = "push-id=%d" % pushId
                self.setResponseCode(http.NO_CONTENT)
                self.finish()
                return
            elif ctype == 'application/x-wms-pushstart':
                self.debug("Got pushstart!")
                if factory.streamingRequest:
                    self.warning('Already streaming: %r',
                                 factory.streamingRequest)
                    factory.streamingRequest.finish(disconnect=True)

                # We should now be in streaming-POST mode, so finish() is called
                # by the channel at the end.
                return
            else:
                self.debug("Unknown content-type: %s", ctype)
                self.finish(disconnect=True)
                return

        d.addCallback(authenticated)
        return d

    def handleContentChunk(self, data):
        self._bytes_read += len(data)
        if self.channel._streaming_post == self:
            if self.channel.factory.streamingRequest != self:
                self.channel.factory.streamingRequest = self
            self.channel.factory.srcelement.dataReceived(data)
        else:
            return server.Request.handleContentChunk(self, data)

    def requestReceived(self, command, path, version):
        # Can be called in _streaming_post mode twice: once after headers, once
        # at the end
        channel = self.channel
        if channel._streaming_post and channel.length == 0:
            self.debug("Calling finish() on streaming post request")
            channel._streaming_post.finish()
            channel._streaming_post = None
            return
        server.Request.requestReceived(self, command, path, version)

    def finish(self, disconnect=False):
        """An overridden finish to optionally force dropping
        connection even if the channel is in persistent mode."""

        self.debug('finish: %r', self)

        if self.finished:
            self.warning('But finished already I have been!')
            return

        if disconnect:
            # not possible to disconnect the transport after calling
            # finish() because the reference will be gone, so forcing
            # to handle finish in a non-persistent mode (ending in
            # closing the connection)
            self.channel.persistent = 0

        factory = self.channel.factory
        if factory.streamingRequest == self:
            factory.streamingRequest = None

        server.Request.finish(self)

    def connectionLost(self, reason):
        self.debug('lost connection (streaming: %r): %r',
                   bool(self.channel._streaming_post), reason)
        self.finish()

    def __repr__(self):
        return '<%s %s %s [%s]>' % (self.method, self.uri, self.clientproto,
                                    self.getClientIP())


class LoggingTransport(object, log.Loggable):

    def __init__(self, transport):
        self.__dict__["_transport"] = transport

    def __getattr__(self, attr):
        return getattr(self._transport, attr)

    def __setattr(self, attr, value):
        setattr(self._transport, attr, value)

    def _print(self, data):
        lines = data.split('\r\n')
        for l in lines[:-1]:
            self.debug("HTTP Response: %r", l)
        if lines:
            self.debug("HTTP Response: %r", lines[-1])

    def write(self, data):
        self._print(data)
        self._transport.write(data)

    def writeSequence(self, data):
        self._print("".join(data))
        self._transport.writeSequence(data)


class WMSChannel(http.HTTPChannel, log.Loggable):

    LOGGING_PERIOD = 10

    def __init__(self):
        http.HTTPChannel.__init__(self)

        self._streaming_post = None
        self._is_authenticated = False
        self._raw_bytes = 0
        self.__transport = None

        self._loggingDC = None

    def makeConnection(self, transport):
        newTrans = LoggingTransport(transport)
        http.HTTPChannel.makeConnection(self, newTrans)

    def lineReceived(self, line):
        self.debug("HTTP Request: %r", line)
        http.HTTPChannel.lineReceived(self, line)

    def rawDataReceived(self, data):
        size = min(self.length, len(data))
        self._raw_bytes += size
        http.HTTPChannel.rawDataReceived(self, data)

    def allHeadersReceived(self):
        last = self.requests[-1]
        last._length = self.length
        http.HTTPChannel.allHeadersReceived(self)
        if self._command == 'POST' and self.length > 0:
            self.debug("Setting new streaming post request")
            self._streaming_post = self.requests[-1]
            self._streaming_post.requestReceived(
                self._command, self._path, self._version)

    # local implementations adding debugging info...
    def connectionMade(self):
        peer = self.transport.getPeer()
        self.info('connection made from: %s:%d', peer.host, peer.port)
        http.HTTPChannel.connectionMade(self)
        self._startLogging()

    def connectionLost(self, reason):
        self._stopLogging()
        peer = self.transport.getPeer()
        self.info('lost connection to: %s:%d (streaming: %r) (%r)',
                   peer.host, peer.port, bool(self._streaming_post), reason)
        http.HTTPChannel.connectionLost(self, reason)

    def requestDone(self, request):
        self.debug('request done: %r (l: %r)', request, self.length)
        http.HTTPChannel.requestDone(self, request)

    def _startLogging(self):
        if self._loggingDC is None:
            self._logState()

    def _stopLogging(self):
        if self._loggingDC is not None:
            self._loggingDC.cancel()
            self._loggingDC = None

    def _logState(self):
        if self.requests:
            last = self.requests[-1]
            self.debug("Connection: %d Bytes; "
                       "Request: %d / %d Bytes (%0.2f %%)",
                       self._raw_bytes, last._bytes_read, last._length,
                       float(last._bytes_read) * 100 / last._length)
        else:
            self.debug("Connection: %d Bytes; No active request",
                       self._raw_bytes)
        self._loggingDC = reactor.callLater(self.LOGGING_PERIOD,
                                            self._logState)

class WMSPushFactory(http.HTTPFactory):
    protocol = WMSChannel
    requestFactory = WMSRequest

    def __init__(self, auth, srcelement):
        # Disable the timeout, server should not close streaming connections
        http.HTTPFactory.__init__(self, timeout=None)

        self.digester = auth
        self.srcelement = srcelement

        self.streamingRequest = None

    def buildProtocol(self, addr):
        channel = http.HTTPFactory.buildProtocol(self, addr)
        channel.requestFactory = self.requestFactory
        return channel

class WMSPullProtocol(basic.LineReceiver):
    timeout = 30
    factory = None
    _timeoutCL = None

    def connectionMade(self):
        self.factory.resetDelay()

        self._headers = []
        self._lastReceived = time.time()

        self.writeRequest()

        self._timeoutCL = reactor.callLater(self.timeout,
            self._timeoutConnection)

    def connectionLost(self, reason):
        if self._timeoutCL:
            self._timeoutCL.cancel()

    def writeRequest(self):
        self.transport.write("GET / HTTP/1.0\r\n")
        self.transport.write("User-Agent: NSServer/1.0,Flumotion/0.0\r\n")
        self.transport.write("\r\n")

    def lineReceived(self, line):
        if line == '':
            # Headers done...
            self.processHeaders()
            self.factory.srcelement.resetASFParser()
            self.setRawMode()
        else:
            self._headers.append(line) # No parsing yet...

    def processHeaders(self):
        # nothing checked here a.t.m...
        pass

    def rawDataReceived(self, data):
        self._lastReceived = time.time()

        self.factory.srcelement.dataReceived(data)

    def _timeoutConnection(self):
        now = time.time()
        if now - self._lastReceived > self.timeout:
            self.transport.loseConnection()
            self._timeoutCL = None
        else:
            self._timeoutCL = reactor.callLater(self.timeout,
                self._timeoutConnection)

class WMSPullFactory(protocol.ReconnectingClientFactory):
    protocol = WMSPullProtocol
    maxDelay = 300 # Back off up to 5 minutes

    def __init__(self, srcelement):
        self.srcelement = srcelement

    def buildProtocol(self, addr):
        p = protocol.ReconnectingClientFactory.buildProtocol(self, addr)
        p.factory = self
        return p

### Two more (sub)classes for (simplistic) handling of WME streams
### published by WMS

class WMSServerPullProtocol(WMSPullProtocol, log.Loggable):
    logCategory = 'wmsserv-pull'
    def writeRequest(self):
        self.transport.write("GET /%s HTTP/1.0\r\n" % self.factory.path)
        self.transport.write("User-Agent: NSServer/1.0,Flumotion/0.0\r\n")
        self.transport.write("Pragma: xPlayStrm=1\r\n")
        self.transport.write("\r\n")

    def _processStatus(self):
        # status line is supposed to be the first element in the
        # _headers list
        if len(self._headers) < 1:
            self.info('No HTTP response header found?!')
            return None
        status_line = self._headers[0]
        try:
            (version, status, reason) = status_line.split(None, 2)
            status = int(status)
        except ValueError:
            self.info('Could not parse the response status line.')
            return None

        return version, status, reason

    def processHeaders(self):
        # we don't do anything serious here, just log some info to
        # help debug in case of the component staying hungry...
        vsr = self._processStatus()
        if vsr:
            status = vsr[1]
            if status == 200:
                return True
            else:
                self.info('Unexpected HTTP status found: %d', status)

        # if the response is anything but 200, we don't reconnect
        # immediately - we'll reconnect when the other end closes
        # connection or after timeout if there's no data flowing...

class WMSServerPullFactory(WMSPullFactory):
    protocol = WMSServerPullProtocol

    def __init__(self, srcelement, path=''):
        WMSPullFactory.__init__(self, srcelement)
        self.path = path.lstrip('/')

class WindowsMediaServer(feedcomponent.ParseLaunchComponent):
    """
    A component to act (to a Windows Media Encoder client in push mode) like
    a Windows Media Server, in order to accept an ASF stream.
    """

    def init(self):
        self._porterDeferred = None
        self.type = None

    def do_check(self):
        props = self.config['properties']

        if props.get('type', 'master') == 'slave':
            for k in 'socket-path', 'username', 'password':
                if not 'porter-' + k in props:
                    msg = ("slave mode, missing required property"
                           " 'porter-%s'" % k)
                    return defer.fail(errors.ConfigError(msg))
        elif props.get('type', 'master') == 'pull':
            wmsCompat = props.get('wms-compatible', False)
            wmsPath = props.get('wms-path', None)

            msg = None
            if (wmsCompat and not wmsPath):
                msg = ('wms-path not specified for WMS compatibility mode')
            elif (not wmsCompat and wmsPath):
                msg = ("WMS compatibility set without specifying path")
            if msg:
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

        # Watch for data flow through identity to turn hungry/happy as
        # appropriate
        self._inactivatedByPadMonitor = False
        identity = self.pipeline.get_by_name("identity")
        self.debug("Adding pad monitor")
        self._padMonitor = PadMonitor(self, identity.get_pad('src'),
            'identity-source')

        if self.type == 'pull':
            host = self.config['properties'].get('host', 'localhost')
            port = self.config['properties'].get('port', 80)
            wmsPath = self.config['properties'].get('wms-path', None)
            if wmsPath:
                factory = WMSServerPullFactory(self._srcelement, wmsPath)
            else:
                factory = WMSPullFactory(self._srcelement)

            self.info("Pulling from %s:%d", host, port)
            reactor.connectTCP(host, port, factory)
        elif self.type == 'slave':
            # Slaved to a porter...
            factory = WMSPushFactory(self._authenticator, self._srcelement)

            self._porterDeferred = d = defer.Deferred()
            mountpoints = [self.mountPoint]
            self._pbclient = porterclient.HTTPPorterClientFactory(
                factory, mountpoints, d)

            creds = credentials.UsernamePassword(self._porterUsername,
                self._porterPassword)
            self._pbclient.startLogin(creds, self._pbclient.medium)

            self.info('Starting porter login at "%s"', self._porterPath)
            # This will eventually cause d to fire
            reactor.connectWith(
                fdserver.FDConnector, self._porterPath,
                self._pbclient, 10, checkPID=False)
        else:
            # Streamer is standalone.
            factory = WMSPushFactory(self._authenticator, self._srcelement)
            try:
                self.info('Listening on tcp port %d', self.port)
                reactor.listenTCP(self.port, factory)
            except error.CannotListenError:
                t = 'Port %d is not available.' % self.port
                self.warning(t)
                m = Error(T_(N_(
                    "Network error: TCP port %d is not available."), self.port))
                self.addMessage(m)
                self.setMood(moods.sad)
                return defer.fail(errors.ComponentStartHandledError(t))

    def do_stop(self):
        if self.type == 'slave' and self._pbclient:
            return self._pbclient.deregisterPath(self.mountPoint)

    def do_pipeline_playing(self):
        # Override this to not set the component happy; instead do this once
        # both the pipeline has started AND we've logged in to the porter.
        if self._porterDeferred:
            d = self._porterDeferred
            self._porterDeferred = None
        else:
            d = defer.succeed(None)

        d.addCallback(lambda res: \
            feedcomponent.ParseLaunchComponent.do_pipeline_playing(self))
        return d

    def get_pipeline_string(self, properties):
        # We require an element by name for later adding our actual source
        # element (which isn't in the registry, so we can't use it here), and
        # because returning an empty string here isn't allowed.
        return "identity name=identity silent=true"

    def configure_pipeline(self, pipeline, properties):

        enable_error_dumps = properties.get('enable-error-dumps', False)

        pushmode = properties.get('type', 'master') != 'pull'
        self._srcelement = asfparse.ASFSrc("asfsrc", pushmode, enable_error_dumps)

        pipeline.add(self._srcelement)

        identity = pipeline.get_by_name("identity")

        srcpad = self._srcelement.get_pad("src")
        sinkpad = identity.get_pad("sink")

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

    def _setPadMonitorActive(self, name):
        if self._inactivatedByPadMonitor:
            self.setMood(moods.happy)

    def _setPadMonitorInactive(self, name):
        self.setMood(moods.hungry)
        self._inactivatedByPadMonitor = True

class PadMonitor(log.Loggable):
    PAD_MONITOR_PROBE_FREQUENCY = 5.0
    PAD_MONITOR_TIMEOUT = PAD_MONITOR_PROBE_FREQUENCY * 2.5

    def __init__(self, component, pad, name):
        self._last_data_time = 0
        self._component = component
        self._pad = pad
        self._name = name
        self._active = False

        # This dict sillyness is because python's dict operations are atomic
        # w.r.t. the GIL.
        self._probe_id = {}
        self._add_probe_dc = None

        self._add_flow_probe()

        self._check_flow_dc = reactor.callLater(self.PAD_MONITOR_TIMEOUT,
            self._check_flow_timeout)

    def isActive(self):
        return self._active

    def detach(self):
        probe_id = self._probe_id.pop("id", None)
        if probe_id:
            self._pad.remove_buffer_probe(probe_id)

        if self._add_probe_dc:
            self._add_probe_dc.cancel()
            self._add_probe_dc = None

        if self._check_flow_dc:
            self._check_flow_dc.cancel()
            self._check_flow_dc = None

    def _add_flow_probe(self):
        self._probe_id['id'] = self._pad.add_buffer_probe(
            self._flow_watch_probe_cb)
        self._add_probe_dc = None

    def _add_flow_probe_later(self):
        self._add_probe_dc = reactor.callLater(self.PAD_MONITOR_PROBE_FREQUENCY,
            self._add_flow_probe)

    def _flow_watch_probe_cb(self, pad, buffer):
        self._last_data_time = time.time()

        id = self._probe_id.pop("id", None)
        if id:
            # This will be None only if detach() has been called.
            self._pad.remove_buffer_probe(id)

            reactor.callFromThread(self._add_flow_probe_later)

            # Data received! Return to happy ASAP:
            reactor.callFromThread(self._check_flow_timeout_now)

        return True

    def _check_flow_timeout_now(self):
        self._check_flow_dc.cancel()
        self._check_flow_timeout()

    def _check_flow_timeout(self):
        self._check_flow_dc = None

        now = time.time()

        self.log("Checking flow timeout. now %r, last seen data at %r", now,
                 self._last_data_time)

        if self._last_data_time > 0:
            delta = now - self._last_data_time

            if self._active and delta > self.PAD_MONITOR_TIMEOUT:
                self.info("No data received on pad for > %r seconds, setting "
                    "to hungry", self.PAD_MONITOR_TIMEOUT)

                self._component._setPadMonitorInactive(self._name)
                self._active = False
            elif not self._active and delta < self.PAD_MONITOR_TIMEOUT:
                self.info("Receiving data again, flow active")
                self._component._setPadMonitorActive(self._name)
                self._active = True

        self._check_flow_dc = reactor.callLater(self.PAD_MONITOR_TIMEOUT,
            self._check_flow_timeout)

