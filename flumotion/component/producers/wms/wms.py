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

from twisted.internet import reactor, defer, error, protocol
from twisted.protocols import basic
from twisted.web import resource, server, http
from twisted.cred import credentials

from flumotion.component import feedcomponent
from flumotion.common import log, errors, messages, keycards
from flumotion.common.planet import moods

from flumotion.twisted import fdserver
from flumotion.component.misc.porter import porterclient
from flumotion.component.component import moods

from flumotion.component.producers.wms import asfparse

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
                if self.channel.factory.streamingRequest:
                    self.warning("Already streaming; dropping existing "
                        "connection")
                    self.channel.factory.streamingRequest.finish()
                    self.channel.factory.streamingRequest = None
                    self.channel.factory.srcelement.resetASFParser()

                if pushId:
                    self.headers["Set-Cookie"] = "push-id=%d" % pushId
                self.setResponseCode(http.NO_CONTENT)
                self.finish()
                return
            elif ctype == 'application/x-wms-pushstart':
                self.debug("Got pushstart!")
                # We should now be in streaming-POST mode, so finish() is called
                # by the channel at the end.
                return
            else:
                self.debug("Unknown content-type: %s", ctype)
                self.finish()
                return

        d.addCallback(authenticated)
        return d

    def handleContentChunk(self, data):
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
        
class WMSChannel(http.HTTPChannel, log.Loggable):

    def __init__(self):
        http.HTTPChannel.__init__(self)

        self._streaming_post = None
        self._is_authenticated = False

    def allHeadersReceived(self):
        http.HTTPChannel.allHeadersReceived(self)
        if self._command == 'POST' and self.length > 0:
            self.debug("Setting new streaming post request")
            self._streaming_post = self.requests[-1]
            self._streaming_post.requestReceived(
                self._command, self._path, self._version)

class WMSPushFactory(http.HTTPFactory):
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
            self.factory.srcelement.resetASFParser()
            self.setRawMode()
        else:
            self._headers.append(line) # No parsing yet... 

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

class WindowsMediaServer(feedcomponent.ParseLaunchComponent):
    """
    A component to act (to a Windows Media Encoder client in push mode) like
    a Windows Media Server, in order to accept an ASF stream.
    """
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

        pushmode = props.get('type', 'master') != 'pull'
        self._srcelement = asfparse.ASFSrc("asfsrc", pushmode)
        if props.get("ignore-keyframes", False):
            self._srcelement.asfparser._obeyHasKeyframesFlag = False

        return feedcomponent.ParseLaunchComponent.do_setup(self)

    def do_start(self, *args, **kwargs):

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
            factory = WMSPullFactory(self._srcelement)

            reactor.connectTCP(host, port, factory)
            return feedcomponent.ParseLaunchComponent.do_start(self,
                *args, **kwargs)

        elif self.type == 'slave':
            # Slaved to a porter...
            factory = WMSPushFactory(self._authenticator, self._srcelement)
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
            factory = WMSPushFactory(self._authenticator, self._srcelement)
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
        self.debug("Buffer probe!")

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

        self.log("Checking flow timeout. now %r, last seen data at %r", now, self._last_data_time)

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

