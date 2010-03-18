# Flumotion - a streaming media server
# Copyright (C) 2010 Fluendo, S.L. (www.fluendo.com).
# All rights reserved.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

from twisted.internet import base, address
from twisted.test import proto_helpers
from twisted.web import http

import setup
setup.setup()

from flumotion.common import log, testsuite
from flumotion.component.producers.wms import wms

base.DelayedCall.debug = True


class Auth:

    def authenticate(self, request, pushId):
        self.pushId = pushId
        code, pushId, stale = http.OK, pushId, False
        return code, pushId, stale


class ASFSrc(log.Loggable):
    reset = False

    def resetASFParser(self):
        self.debug('resetting ASF parser...')
        self.reset = True


class TestWMSPush(testsuite.TestCase):

    def setUp(self):
        self.src = src = ASFSrc()

        self.factory = factory = wms.WMSPushFactory(
            auth=Auth(), srcelement=src)
        factory.doStart()

        self.channel = channel = factory.buildProtocol(addr='address')
        self.transport = proto_helpers.StringTransportWithDisconnection(
            peerAddress=address.IPv4Address('TCP', '127.0.0.1', 12345))
        channel.makeConnection(self.transport)
        self.transport.protocol = channel

    def tearDown(self):
        self.transport.loseConnection()
        self.factory.doStop()

    def testPushSetupResetsAsfParser(self):
        self.channel.dataReceived(
            'POST /wmenc.wsf HTTP/1.1\r\n'
            'Content-Type: application/x-wms-pushsetup\r\n'
            '\r\n')
        self.debug('%r', self.transport.value())
        self.assertTrue(self.src.reset)

    def testCookiesWithMultipleKeys(self):
        self.channel.dataReceived(
            'POST /wmenc.wsf HTTP/1.1\r\n'
            'Content-Type: application/x-wms-pushsetup\r\n'
            'Cookie: funny-cookie=2 ; push-id=123 ; hilarious-cookie=3\r\n'
            '\r\n')
        self.debug('%r', self.transport.value())
        self.assertEquals(self.factory.digester.pushId, 123)
