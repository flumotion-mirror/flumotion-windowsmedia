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

from twisted.internet import reactor, defer, error
from twisted.web import resource, server, http
from twisted.cred import credentials

from flumotion.component import feedcomponent
from flumotion.common import log, errors, messages

from flumotion.twisted import fdserver
from flumotion.component.misc.porter import porterclient
from flumotion.component.component import moods

from flumotion.component.producers import asfparse

class HTTPDigestChallenger:
    def __init__(self, username):
        self.username = username
        self.nonce = None
        # We also need A2,  for which we need method and uri. Or we could just
        # send A2?

        self.qop = None # If non-None, the next two must be set
        self.cnonce = None
        self.ncvalue = None

        self.response = None

class HTTPDigestKeycard(Keycard, HTTPDigestChallenger):
    def __init__(self, username):
        HTTPDigestChallenger.__init__(self, username)
        Keycard.__init__(self)

    def getData(self):
        d = Keycard.getData(self)
        d['username'] = self.username
        # Realm? Uri? 
        return d

    def __repr__(self):
        return "<%s %s for requesterId %r in state %s>" % (
            self.__class__.__name__, self.username, 
            self.requesterId, _statesEnum[self.state])

pb.setUnjellyableForClass(HTTPDigestKeycard, HTTPDigestKeycard)

class DigestBouncer(bouncer.Bouncer):
    keycardClasses = (HTTPDigestKeycard,)

    timeout = 60*60*3  # 3 hours.

    def init(self):
        self._challenges = {} # {opaque -> (nonce, timestamp)}
                             # Issued challenges. It's valid to reuse an old 
                             # challenge with a new keycard, so we don't check 
                             # keycard.id. 

    def _generateRandomString(self, numchars):
        """
        Generate a random US-ASCII string of length numchars
        """
        str = ""
        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        for _ in range(numchars):
            str += chars[random.randint(0, len(chars)-1)]

        return str

    def _generateOpaqueAndNonce(self):
        opaque = self._generateRandomString(16)
        nonce = self._generateRandomString(16)

        self._challenges[opaque] = (nonce, time.time())
        return (opaque, nonce)

    def _cleanupChallenges(self):
        now = time.time()
        for (opaque, (nonce,ts)) in self._challenges.items():
            if now - ts > self.timeout:
                del self._challenges[opaque]

    def _checkNonce(self, opaque, nonce):
        self._cleanupChallenges()
        if opaque not in self._challenges:
            self.debug("opaque not in outstanding")
            return False
        (expectednonce, ts) = self._challenges[opaque]

        if expectednonce != nonce:
            self.debug("nonce doesn't correspond to opaque")
            return False

    def do_authenticate(self, keycard):
        self.addKeycard(keycard)

        if not keycard.nonce or not keycard.opaque:
            # We haven't issued this keycard a nonce yet; do so.
            (opaque,nonce) = self._generateOpaqueAndNonce()

            keycard.nonce = nonce
            keycard.opaque = opaque
            keycard.stale = False

            return keycard
        elif not self._checkNonce(keycard.opaque, keycard.nonce):
            # This nonce is bad (old or invalid); send a 'stale' response
            keycard.stale = True
        else:
            # nonce is ok! Try authenticating.
            pass

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



class DigestAuth(log.Loggable):
    logCategory = "digestauth"

    _qop_type = 'auth' # Others not implemented
    _algorithm = 'MD5' # May also be set to 'MD5-sess'

    def __init__(self, realm):
        self._outstanding = {} # opaque -> (nonce, timestamp)
        self._pushIds = {} # pushid -> authenticated
        self._realm = realm
        self._users = {}

    def addUser(self, username, password):
        self._users[username] = password

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

