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

"""
Windows Media Service Pull-Mode producer.
Try to look like a Windows Media Encoder.

http://msdn.microsoft.com/en-us/library/cc251167(PROT.10).aspx
"""

import random
import time

from twisted.internet import reactor, error
from twisted.web import http

from flumotion.common import log

from flumotion.component.common import http as fhttp
from flumotion.component.common.wms import common, mmsproducer


# Pretend to be Windows Media Encoder
SERVER_IDENT = "Rex/12.0.7600.16385 (Flumotion Streaming Server)"

LOG_CATEGORY = "wms-pull"


class WMSPullRequest(fhttp.Request):

    def __init__(self, channel, info, active):
        fhttp.Request.__init__(self, channel, info, active)
        self.agent = None
        self.version = None
        self.requested_client_id = None
        self.client_id = None

    def onInitiate(self):
        if self.method != "GET":
            self.warning("Pull requests must use GET")
            self.error(http.NOT_ALLOWED)

        required = ["Host", "User-Agent"]
        for name in required:
            if self.getRecvHeader(name) is None:
                self.warning("Required header %s not found", name)
                self.error(http.BAD_REQUEST)

        agent, version = self.parseUserAgent()
        self.agent = agent
        self.version = version

        # Force HTTP 1.0 response
        self.protocol = fhttp.HTTP10

        client_id = None
        for pragma in self.getRecvHeader("Pragma"):
            if pragma.startswith("client-id="):
                try:
                    client_id = int(pragma[10:])
                except ValueError:
                    pass

        self.requested_client_id = client_id
        self.client_id = self.channel.factory.getClientId(client_id)

        self.setHeader("Server", SERVER_IDENT)
        self.setHeader("Cache-Control", "no-cache")
        self.setHeader("Pragma", "no-cache"),
        self.setHeader("Pragma", "client-id=%d" % self.client_id)
        self.setHeader("Pragma", "features=\"broadcast\"")

    def onActivate(self):
        pass

    def onDataReceived(self, data):
        pass

    def onAllContentReceived(self):
        pass

    def onConnectionLost(self, reason):
        pass

    ### MMS Producer callbacks ###

    def pushData(self, producer, data):
        self.write(data)


class WMSPullDescribeRequest(WMSPullRequest):

    def onInitiate(self):
        WMSPullRequest.onInitiate(self)
        self.setHeader("Content-Type", "application/vnd.ms.wms-hdr.asfv1")

    def onActivate(self):
        header = self.channel.factory.getMMSHeader(self)

        if header is None:
            self.setResponseCode(http.NOT_FOUND)
            self.finish()
            return

        self.setResponseCode(http.OK)
        self.setLength(len(header))
        self.pushData(None, header)
        self.finish()


class WMSPullPlayRequest(WMSPullRequest):

    def onInitiate(self):
        WMSPullRequest.onInitiate(self)

        req_clid = self.requested_client_id
        if req_clid is not None and req_clid != self.client_id:
            self.setHeader("Pragma", "xResetStrm=1")

        self.setHeader("Content-Type", "application/x-mms-framed")

    def onActivate(self):
        self.setResponseCode(http.OK)
        if not self.channel.factory.registerPull(self):
            self.setResponseCode(http.NOT_FOUND)
            self.finish()

    def connectionLost(self, reason):
        self.channel.factory.removePull(self)


class WMSPullRequestFactory(fhttp.Requestfactory):

    def buildRequest(self, channel, info, active):
        if info.method == "POST":
            # just silently ignore POST request
            return fhttp.ErrorRequest(channel, info, active, http.OK)

        agent = info.headers.get("user-agent")
        name, version, = fhttp.parseUserAgent(agent)
        if name not in ["NSServer", "NSPlayer"] or version is None:
            self.warning("Agent not supported: %s", agent)
            return fhttp.ErrorRequest(channel, info, active, http.FORBIDDEN)
        if name == "NSPlayer" or name == "NSServer" and version >= (7,0):
            pragmas = info.headers.get("pragma")
            if not (pragmas and "xPlayStrm=1" in pragmas):
                return WMSPullDescribeRequest(channel, info, active)
        return WMSPullPlayRequest(channel, info, active)


class WMSPullFactory(fhttp.Factory):

    requestFactoryClass = WMSPullRequestFactory

    session_timeout = 5*60
    expire_period = 60

    def __init__(self):
        fhttp.Factory.__init__(self)

        self.header_obj = None
        self.data_obj = None

        self._requests = {} # {CLIENT_ID: Request}
        self._producers = {} # {CLIENT_ID: Producer}
        self._timeouts = {} # {CLIENT_ID: EXPIRATION_TIME}
        self._start_time = time.time()

        self._call = None
        self._expire()

    def stop(self):
        if self._call is not None:
            self._call.cancel()
            self._call = None
        for req in self._requests.values():
            req.finish()
        self._requests.clear()
        self._producers.clear()
        self._timeouts.clear()

    def getClientId(self, requested_id):
        if requested_id in self._producers:
            return requested_id
        client_id = int((time.time() - self._start_time) * 100) % 2147483648
        assert client_id not in self._producers, "Duplicated client identifier"
        return client_id

    def removePull(self, req):
        client_id = req.client_id
        if client_id in self._requests:
            # The request may have changed
            if self._requests[client_id] == req:
                if client_id in self._producers:
                    self.debug("Disconnecting request from MMS producer %d",
                               client_id)
                    self._producers[client_id].register(None)
                del self._requests[client_id]

    def getMMSHeader(self, req):
        if self.header_obj is None:
            return None

        producer = self._getProducer(req.client_id)
        producer.reset() # Should we reset the producer ?

        data = self.header_obj.data + self.data_obj.data

        return producer.mms_header(data)

    def registerPull(self, req):
        assert req not in self._requests, "Already registered"

        if self.header_obj is None:
            return False

        client_id = req.client_id

        if client_id in self._requests:
            self.debug("Closing old request for MMS producer %d", client_id)
            self._requests[client_id].finish()
            del self._requests[client_id]

        producer = self._getProducer(client_id)
        producer.reset() # Should we really reset the producer ?
        self.debug("Connecting request to MMS producer %d", client_id)
        producer.register(req)
        producer.pushHeaders(self.header_obj, self.data_obj)

        self._requests[client_id] = req
        return True

    def pushHeaders(self, header_obj, data_obj):
        expiration = time.time() + self.expire_period

        self.header_obj = header_obj
        self.data_obj = data_obj

        for client_id, request in self._requests.items():
            producer = self._producers[client_id]
            producer.pushHeaders(header_obj, data_obj)

            request.channel.resetTimeout("inactivity")
            self._timeouts[client_id] = expiration

    def pushPacket(self, packet):
        expiration = time.time() + self.expire_period

        for client_id, request in self._requests.items():
            producer = self._producers[client_id]
            producer.pushPacket(packet)

            request.channel.resetTimeout("inactivity")
            self._timeouts[client_id] = expiration

    def _getProducer(self, client_id):
        producer = self._producers.get(client_id)
        if producer is None:
            producer = mmsproducer.MMSProducer()
            self._producers[client_id] = producer
        return producer

    def _expire(self):
        now = time.time()
        for client_id, expiration in self._timeouts.items():
            if  expiration < now:
                self.debug("MMS producer %d expired", client_id)
                if client_id in self._producers:
                    self._producers[client_id].stop()
                    del self._producers[client_id]
                if client_id in self._requests:
                    self._requests[client_id].finish()
                    del self._requests[client_id]
                del self._timeouts[client_id]
        self._call = reactor.callLater(self.expire_period, self._expire)
