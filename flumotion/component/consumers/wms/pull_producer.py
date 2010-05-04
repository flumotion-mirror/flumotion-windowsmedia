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

        self.setHeader("Server", SERVER_IDENT)
        self.setHeader("Cache-Control", "no-cache")
        self.setHeader("Pragma", "no-cache"),
        self.setHeader("Pragma", "client-id=%d" % self.channel.client_id)
        self.setHeader("Pragma", "features=\"broadcast,playlist\"")

    def onActivate(self):
        pass

    def onDataReceived(self, data):
        pass

    def onAllContentReceived(self):
        pass

    def onConnectionLost(self, reason):
        pass


class WMSPullDescribeRequest(WMSPullRequest):

    def onInitiate(self):
        WMSPullRequest.onInitiate(self)
        self.setHeader("Content-Type", "application/vnd.ms.wms-hdr.asfv1")

    def onActivate(self):
        factory = self.channel.factory
        if factory.header is None:
            self.setResponseCode(http.NOT_FOUND)
            self.finish()
            return

        self.setResponseCode(http.OK)
        self.setLength(len(factory.header))
        self.write(factory.header)
        self.finish()


class WMSPullPlayRequest(WMSPullRequest):

    def onInitiate(self):
        WMSPullRequest.onInitiate(self)
        self.setHeader("Content-Type", "application/x-mms-framed")

    def onActivate(self):
        self.setResponseCode(http.OK)
        self.channel.factory.registerPull(self)

    def connectionLost(self, reason):
        self.channel.factory.removePull(self)


class WMSPullRequestFactory(fhttp.Requestfactory):

    def buildRequest(self, channel, info, active):
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

    def __init__(self):
        fhttp.Factory.__init__(self)
        #TODO: better client id ?
        self.client_id = 0
        self.header = None

        self._requests = set()

    def genClientId(self):
        self.client_id += 1
        return self.client_id

    def buildProtocol(self, addr):
        channel = fhttp.Factory.buildProtocol(self, addr)
        channel.client_id = self.genClientId()
        return channel

    def registerPull(self, req):
        req.write(self.header)
        self._requests.add(req)

    def removePull(self, req):
        self._requests.remove(req)

    ### MMS Producer callbacks ###

    def setHeader(self, producer, data):
        self.header = data

    def pushData(self, producer, data):
        for req in self._requests:
            req.write(data)
