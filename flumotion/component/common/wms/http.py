# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006 Fluendo, S.L. (www.fluendo.com).
# All rights reserved.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

from twisted.internet import reactor, protocol
from twisted.protocols import basic
from twisted.web import http

from flumotion.common import log, common

RAW_DATA = 0
LINE_DATA = 1

CONNECT_TIMEOUT = 60 # Maximum time after connection to receive data
REQUEST_TIMEOUT = 60 # Maximum time after request start to receive all headers
IDLE_TIMEOUT = 60 # Maximum time without receiving any data
INACTIVITY_TIMEOUT = 300 # Maximum time between two requests

HTTP_MULTIPLE_FIELD_HEADERS = set(["accept",
                                   "accept-charset",
                                   "accept-encoding",
                                   "accept-language",
                                   "accept-ranges",
                                   "allow",
                                   "cache-control",
                                   "connection",
                                   "content-encoding",
                                   "content-language",
                                   "expect",
                                   "pragma",
                                   "proxy-authenticate",
                                   "te",
                                   "trailer",
                                   "transfer-encoding",
                                   "upgrade",
                                   "via",
                                   "warning",
                                   "www-authenticate",
                                   # extensions for wms
                                   "x-accept-authentication",
                                   "supported"])


def parseUserAgent(agent_header):
    agents = agent_header.split("", 1)
    parts = agents[0].split(",", 1)
    name, version = parts[0].split("/", 1)
    digits = version.split(".")

    def try2convert(s):
        try:
            int(s)
        except ValueError:
            s

    return name, tuple([try2convert(s) for s in digits])


class HTTPError(Exception):
    """
    If this error is raise inside one of the request
    virtual method, the request will be terminated
    with the specified status code and message.
    """

    def __init__(self, *args, **kwargs):
        self.status_code = http.INTERNAL_SERVER_ERROR
        self.status_message = None
        if "code" in kwargs:
            self.status_code = kwargs.pop("code")
        if "message" in kwargs:
            self.status_message = kwargs.pop("message")
        Exception.__init__(self, *args, **kwargs)


class BaseRequest(object):
    """
    Abstract class to define the public method Channel needs.
    """

    def __init__(self, channel):
        self.channel = channel

    def initiate(self):
        """Called by the channel when the request has been setup."""

    def activate(self):
        """Called by the channel when data can be written to the transport."""

    def dataReceived(self, data):
        """Called by the channel when data is received"""

    def allContentReceived(self):
        """Called by the channel when all the content has been received"""

    def connectionLost(self, reason):
        """Called by the channel when the connection has been lost"""


class Request(BaseRequest, log.Loggable):
    """
    Base class for HTTP requests.

    Requests are created by the RequestFactory AFTER the channel
    received the request line and all the headers. This way,
    the factory can create different type of request in function
    of the request/headers.
    """

    logCategory = "http-request"
    log_ident = "request"
    data_format = RAW_DATA

    def __init__(self, channel, info, active):
        peer = channel.transport.getPeer()
        self.logName = "%s:%s" % (peer.host, peer.port)
        self.debug('Creating %s', self.log_ident)

        BaseRequest.__init__(self, channel)

        self.protocol = info.protocol
        self.method = info.method
        self.uri = info.uri

        self._received_headers = {}
        self._received_cookies = {}
        self._received_length = None
        self.received_bytes = 0

        self._headers = {}
        self._cookies = {}

        self._length = 0
        self.bytes_sent = 0 # Bytes of BODY written (headers do not count)

        self.initiated = False # If the request has been initiated
        self.activated = False # If the request can write directly to the channel
        self.writing = False   # If already started writing data
        self.finished = False  # If the request is finished
        self.persistent = True # If the connection must be kept
        self.received = False  # If all body content received

        self.status_code = http.NO_CONTENT
        self.status_message = http.RESPONSES[self.status_code]

        self._parseHeaders(info.headers)

        self.setLength(0)

        self.transport = None
        if not active:
            self.transport = http.StringTransport()

    ### Virtual Methods ###

    def onInitiate(self):
        pass

    def onActivate(self):
        pass

    def onDataReceived(self, data):
        pass

    def onAllContentReceived(self):
        pass

    def onConnectionLost(self, reason):
        pass

    ### Public Methods ###

    def initiate(self):
        self.debug('Initiating %s', self.log_ident)
        assert not self.initiated, "Already initiated"
        self.initiated = True

        if self.transport is None:
            self.activate()

        try:
            self.onInitiate()
        except HTTPError, e:
            self._makeError(e.status_code, e.status_message)

    def activate(self):
        self.debug('Activating %s', self.log_ident)
        assert not self.activated, "request already active"
        self.activated = True

        old = self.transport
        self.transport = self.channel.transport
        if old is not None:
            self.transport.write(old.getvalue())

        try:
            self.onActivate()
        except HTTPError, e:
            self._makeError(e.code, e.message)

        if self.finished:
            reactor.callLater(0, self._cleanup)

    def dataReceived(self, data):
        self.received_bytes += len(data)

        if self.finished:
            return

        try:
            self.onDataReceived(data)
        except HTTPError, e:
            self._makeError(e.code, e.message)
            return

    def allContentReceived(self):
        self.debug('All content received on %s', self.log_ident)
        assert not self.received, "Already been notified"

        if self.finished:
            return

        self.received = True
        try:
            self.onAllContentReceived()
        except HTTPError, e:
            self._makeError(e.code, e.message)
            return

    def connectionLost(self, reason):
        self.debug('Connection lost for %s: %s', self.log_ident,
                   reason.getErrorMessage())
        try:
            self.onConnectionLost(reason)
        except HTTPError:
            pass

    def error(self, code, message=None):
        self.debug('Error %d on %s', code, self.log_ident)
        assert not self.writing, "Header already sent"
        self._makeError(code, message)
        raise HTTPError(code=code, message=message)

    def finish(self):
        self.debug('Finishing %s', self.log_ident)
        assert not self.finished, "Request already finished"
        self.finished = True

        self.writeHeaders()

        # If not all the body has been read, we must disconnect
        if not self.received:
            self.persistent = False

        if self.activated:
            reactor.callLater(0, self._cleanup)

    def writeHeaders(self):
        if not self.writing:
            self.debug('Writing headers on %s', self.log_ident)
            self.writing = True

            lines = []
            lines.append("%s %s %s" % (self.protocol, self.status_code,
                                       self.status_message))

            for name, value in self._headers.items():
                capname = '-'.join([p.capitalize() for p in name.split('-')])
                if name in HTTP_MULTIPLE_FIELD_HEADERS:
                    lines.append("%s: %s" % (capname, ", ".join(value)))
                else:
                    lines.append("%s: %s" % (capname, value))

            for name, payload in self._cookies.items():
                cookie = "%s=%s" % (name, payload)
                lines.append("%s: %s" % ("Set-Cookie", cookie))

            seq = []
            for line in lines:
                self.log("<<< %s", line)
                seq.append(line)
                seq.append("\r\n")
            seq.append("\r\n")

            self.transport.writeSequence(seq)

    def write(self, data):
        self.writeHeaders()

        if data:
            if len(data) + self.bytes_sent > self._length:
                raise HTTPError("Ask to send more data than "
                                "the specified content length")
            self.bytes_sent += len(data)
            self.transport.write(data)

    def hasRecvHeader(self, name):
        return name.lower() in self._received_headers

    def getRecvHeader(self, name):
        return self._received_headers.get(name.lower())

    def getRecvLength(self):
        return self._received_length

    def getRecvCookie(self, name):
        return self._received_cookies.get(name)

    def hasHeader(self, name):
        return name.lower() in self._headers

    def getHeader(self, name):
        return self._headers.get(name.lower())

    def setHeader(self, name, value):
        assert not self.writing, "Header already sent"
        header = name.lower()
        if header in HTTP_MULTIPLE_FIELD_HEADERS:
            if header not in self._headers:
                self._headers[header] = []
            fields = self._headers[header]
            if isinstance(value, list):
                fields.extend(value)
            else:
                fields.extend([f.strip() for f in value.split(",")])
        self._headers[header] = value

    def clearHeaders(self):
        assert not self.writing, "Header already sent"
        self._headers.clear()

    def removeHeader(self, name):
        assert not self.writing, "Header already sent"
        del self._headers[name.lower()]

    def setLength(self, length):
        assert not self.writing, "Header already sent"
        self._length = length
        self.setHeader("content-length", int(length))

    def setResponseCode(self, code, message=None):
        assert not self.writing, "Header already sent"
        self.status_code = code
        if message:
            self.status_message = message
        else:
            self.status_message = http.RESPONSES.get(self.status_code,
                                                     "Unknown Status")

    def addCookie(self, name, payload):
        assert not self.writing, "Header already sent"
        self._cookies[name] = payload

    def parseUserAgent(self):
        agent = self.getRecvHeader("user-agent")
        if not agent:
            return "unknown", None
        parseUserAgent(agent)


    ### Private Methods ###

    def _makeError(self, code, message=None):
        self.persistent = False
        if not self.finished:
            if not self.writing:
                self.setResponseCode(code, message)
                self.clearHeaders()
            self.finish()

    def _callEach(self, method, *args, **kwargs):
        for proc in common.get_all_methods(self, method, False):
            proc(self, *args, **kwargs)
            if self.finished:
                break

    def _parseHeaders(self, headers):
        for name, value in headers.items():
            key = name.lower()
            self._received_headers[key] = value

            if key == 'content-length':
                self._received_length = int(value)

            # Check if the connection should be kept alive
            if key == 'connection':
                tokens = map(str.lower, value.split(' '))
                if 'close' in tokens:
                    # Set the response header to match
                    self.setHeader('connection', 'close')
                    self.persistent = False
                else:
                    self.persistent = True

            # Parse cookies
            if key == 'cookie':
                for cook in value.split(';'):
                    cook = cook.lstrip()
                    try:
                        k, v = cook.split('=', 1)
                        self._received_cookies[k] = v
                    except ValueError:
                        pass

    def _cleanup(self):
        self.debug('%s done; received %s out of %s bytes',
                   self.log_ident, self.received_bytes, self._received_length)

        self.channel.requestDone(self)
        del self.channel


class ErrorRequest(BaseRequest):
    """
    Request that just write an error and finish.

    Can be used by the request factory in case of error.
    """

    def __init__(self, channel, info, active, code, message=None):
        BaseRequest.__init__(self, channel)

        self.protocol = info.protocol
        self.status_code = code
        self.status_message = http.RESPONSES[self.status_code]
        self.persistent = False
        if active:
            self.activate()

    def activate(self):
        response = "%s %s %s\r\n\r\n" % (self.protocol, self.status_code,
                                         self.status_message)
        self.channel.transport.write(response)
        reactor.callLater(0, self._cleanup)

    def _cleanup(self):
        self.channel.requestDone(self)
        del self.channel


class Requestfactory(object):

    def buildRequest(self, channel, info, active):
        return Request(channel, info, active)


class TimeoutMixin(object):

    _timeouts = None # {TIMEOUT_NAME: (TIMEOUT, CALLBACK)}
    _callids = None # {TIMEOUT_NAME: IDelayedCall}

    def addTimeout(self, name, duration, callback):
        self._lazySetup()
        self._timeouts[name] = (duration, callback)

    def resetTimeout(self, name):
        assert name in self._timeouts, "Unknown timeout " + name
        self.cancelTimeout(name)
        duration = self._timeouts[name][0]
        dc = reactor.callLater(duration, self._onTimeout, name)
        self._callids[name] = dc

    def cancelTimeout(self, name):
        assert name in self._timeouts, "Unknown timeout " + name
        if name in self._callids:
            dc = self._callids.pop(name)
            dc.cancel()

    def cancelAllTimeouts(self):
        for dc in self._callids.values():
            dc.cancel()
        self._callids.clear()

    def _lazySetup(self):
        if self._timeouts is None:
            self._timeouts = {}
            self._callids = {}

    def _onTimeout(self, name):
        del self._callids[name]
        self._timeouts[name][1]()


class RequestInfo(object):

    def __init__(self):
        self.protocol = None
        self.headers = {}
        self.method = None
        self.uri = None


class Channel(TimeoutMixin, basic.LineReceiver, log.Loggable):

    logCategory = "http-channel"
    requestFactory = None
    max_headers = 20
    protocol = "HTTP/1.1"

    STATE_REQLINE = 0
    STATE_HEADERS = 1
    STATE_BODY = 2

    connect_timeout = CONNECT_TIMEOUT
    request_timeout = REQUEST_TIMEOUT
    idle_timeout = IDLE_TIMEOUT
    inactivity_timeout = INACTIVITY_TIMEOUT

    def __init__(self):
        self.authenticated = False

        self._requests = []

        self.addTimeout("connect", self.connect_timeout,
                        self._onConnectionTimeout)
        self.addTimeout("request", self.request_timeout,
                        self._onRequestTimeout)
        self.addTimeout("idle", self.idle_timeout,
                        self._onIdleTimeout)
        self.addTimeout("inactivity", self.inactivity_timeout,
                        self._onInactivityTimeout)

        self._reset()

        self.debug("Push channel created")

    def requestDone(self, request):
        """Called by the active request when it is done writing"""
        assert request == self._requests[0], "Unexpected request done"
        del self._requests[0]

        if request.persistent:
            if self._requests:
                self._requests[0].activate()
        else:
            self.transport.loseConnection()

    def close(self):
        self.debug("Channel closed")
        for request in list(self._requests):
            request.finish()
        self.transport.loseConnection()

    ### Virtual Methods ###

    def onConnectionMade(self):
        pass

    def onConnectionLost(self, reason):
        pass

    ### Overridden Methods ###

    def connectionMade(self):
        peer = self.transport.getPeer()
        self.logName = "%s:%s" % (peer.host, peer.port)
        self.debug('Connection made')
        self.resetTimeout("connect")
        self.onConnectionMade()

    def lineReceived(self, line):
        self.cancelTimeout("connect")
        self.resetTimeout("idle")

        if self._state == self.STATE_REQLINE:
            # We are waiting for a request line
            self._gotRequestLine(line)
        elif self._state == self.STATE_HEADERS:
            # We are waiting for a header line
            self._gotHeaderLine(line)
        else:
            self.log("Content: %s", line)
            self._handleReceived(line)

    def rawDataReceived(self, data):
        self.log("Content: %d bytes out of %d, %d bytes remaining",
                 len(data), self._length, self._remaining)
        self.resetTimeout("idle")
        self._handleReceived(data)

    def connectionLost(self, reason):
        self.debug('Connection lost: %s', reason.getErrorMessage())

        self.cancelAllTimeouts()

        for request in self._requests:
            request.connectionLost(reason)
        self._cleanup()

        self.onConnectionLost(reason)

    ### Private Methods ###

    def _onConnectionTimeout(self):
        self.warning("Connection timeout")
        self._httpTimeout()

    def _onRequestTimeout(self):
        self.warning("Request timeout")
        self._httpTimeout()

    def _onInactivityTimeout(self):
        self.warning("Inactivity timeout")
        self._httpTimeout()

    def _onIdleTimeout(self):
        self.warning("Idle timeout")
        self._httpTimeout()

    def _httpBadRequest(self):
        self._httpError(400)

    def _httpInternalServerError(self):
        self._httpError(500)

    def _httpVersionNotSupported(self):
        self._httpError(505)

    def _httpTimeout(self):
        self._httpError(408)

    def _httpError(self, code, message=None):

        def respond():
            msg = message or http.RESPONSES.get(code, "Unknown Status")
            resp = "%s %d %s\r\n\r\n" % (self.protocol, code, msg)
            self.transport.write(resp)

        # If we can, respond with the error status
        if self._requests:
            if not self._requests[0].writing:
                respond()
        else:
            if self._reqinfo:
                respond()

        self.transport.loseConnection()

    def _cleanup(self):
        self._reset()
        self._requests = None

    def _reset(self):
        self._state = self.STATE_REQLINE
        self._header = ''
        self._length = 0
        self._remaining = 0
        self._reqinfo = None
        self.debug('Ready for new request')

    def _handleReceived(self, data):
        assert self._requests, "No receiving request"
        request = self._requests[-1]
        if len(data) < self._remaining:
            request.dataReceived(data)
            self._remaining -= len(data)
        else:
            expected = data[:self._remaining]
            request.dataReceived(expected)
            extraneous = data[self._remaining:]
            self._remaining -= len(expected)
            self._gotAllContent()
            self.setLineMode(extraneous)

    def _gotRequestLine(self, line):
        self.log(">>> %s", line)
        assert self._state == self.STATE_REQLINE

        self.cancelTimeout("inactivity")
        self.resetTimeout("request")

        parts = line.split()
        if len(parts) != 3:
            self._httpBadRequest()
            return

        method, uri, protocol = parts
        if protocol != self.protocol:
            self._httpVersionNotSupported()
            return

        assert self._reqinfo is None, "Already have a request info"
        self._reqinfo = RequestInfo()
        self._reqinfo.protocol = protocol
        self._reqinfo.method = method
        self._reqinfo.uri = uri

        # Now waiting for headers
        self._state = self.STATE_HEADERS

    def _gotHeaderLine(self, line):
        assert self._state == self.STATE_HEADERS

        if line == '':
            if self._header:
                self._gotHeaderEntry(self._header)
            self._header = ''
            self._gotAllHeaders()
        elif line[0] in ' \t':
            # Multi-lines header
            self._header = self._header + '\n' + line
        else:
            if self._header:
                self._gotHeaderEntry(self._header)
            self._header = line

    def _gotHeaderEntry(self, line):
        self.debug(">>> %s", line)
        assert self._reqinfo is not None, "No request info"

        info = self._reqinfo

        header, data = line.split(':', 1)
        header = header.lower()
        data = data.strip()

        if header == 'content-length':
            self._length = int(data)
            self._remaining = int(data)

        if header in HTTP_MULTIPLE_FIELD_HEADERS:
            if header not in info.headers:
                info.headers[header] = []
            fields = info.headers[header]
            fields.extends([f.strip() for f in data.split(",")])
        else:
            info.headers[header] = data

        if len(info.headers) > self.max_headers:
            self._httpBadRequest()

    def _gotAllHeaders(self):
        self.debug("All headers received")
        assert self._state == self.STATE_HEADERS
        assert self._reqinfo is not None, "No request info"

        self.cancelTimeout("request")

        info = self._reqinfo
        activate = len(self._requests) == 0
        request = self.requestFactory.buildRequest(self, info, activate)

        self._requests.append(request)

        request.initiate()

        self._state = self.STATE_BODY

        if self._remaining == 0:
            self._gotAllContent()
        elif request.data_format == RAW_DATA:
            self.setRawMode()

    def _gotAllContent(self):
        self.debug("All content received")
        assert self._requests, "No receiving request"
        self._reset()

        self.cancelTimeout("idle")

        request = self._requests[-1]
        request.allContentReceived()

        self.resetTimeout("inactivity")


class Factory(protocol.ServerFactory):

    protocol = Channel
    requestFactoryClass = Requestfactory

    def __init__(self):
        self._requestfactory = self.requestFactoryClass()

    def buildProtocol(self, addr):
        channel = protocol.ServerFactory.buildProtocol(self, addr)
        channel.requestFactory = self._requestfactory
        return channel
