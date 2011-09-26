# -*- Mode: Python -*-
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
