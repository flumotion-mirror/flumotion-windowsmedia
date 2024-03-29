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

from twisted.internet import defer, error
from twisted.python import failure
from twisted.cred import error as cerror

from flumotion.component.bouncers import component
from flumotion.common import log, keycards, config

class HTTPDigestChecker(log.Loggable):
    def __init__(self, **users):
        self.users = users

    def addUser(self, username, ha1):
        self.users[username] = ha1

    def _cbResponseMatch(self, matched, username):
        if matched:
            self.debug('user %s authenticated' % username)
            return username
        else:
            self.debug('user %s refused, password not matched' % username)
            return failure.Failure(cerror.UnauthorizedLogin())

    def requestAvatarId(self, credentials):
        if credentials.username in self.users:
            ha1 = self.users[credentials.username]
            return defer.maybeDeferred(
                credentials.checkHTTPDigestResponse,
                ha1).addCallback(
                self._cbResponseMatch, credentials.username)
        else:
            self.debug('user %s refused, not in database' %
                credentials.username)
            return defer.fail(cerror.UnauthorizedLogin())

class DigestBouncer(component.Bouncer):
    keycardClasses = (keycards.KeycardHTTPDigest,)

    def init(self):
        self._checker = HTTPDigestChecker()
        self._realm = None

    def do_setup(self):
        props = self.config['properties']
        if 'realm' in props:
            self.realm = props['realm']
        filename = props['filename']
        try:
            f = open(filename)
            lines = f.readlines()
            f.close()

            for line in lines:
                if line[0] == '#': continue

                user, hash = line.strip().split(':')
                self._checker.addUser(user, hash)
        except IOError, e:
            return defer.fail(config.ConfigError(str(e)))

        return defer.succeed(None)

    def do_authenticate(self, keycard):
        def _success(result):
            keycard.state = keycards.AUTHENTICATED
            keycard.avatarId = result
            self.addKeycard(keycard)
            self.debug("Authenticated login for %s", keycard.avatarId)
            return keycard

        def _failure(failure):
            failure.trap(cerror.UnauthorizedLogin)
            self.debug("Failed authentication")
            return None

        self.debug("Received authentication request with keycard %r", keycard)
        if keycard.response:
            d = self._checker.requestAvatarId(keycard)
            d.addCallbacks(_success, _failure)
            return d
        else:
            return None

