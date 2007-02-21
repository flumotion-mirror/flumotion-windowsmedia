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

from flumotion.common import log

class GUID:
    # Only includes the ones we care about for now...

    ASF_UNKNOWN = "\0" * 16
    ASF_HEADER_OBJECT = \
        "\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c"
    ASF_HEADER_FILE_PROPERTIES_OBJECT = \
        "\xa1\xdc\xab\x8c\x47\xa9\xcf\x11\x8e\xe4\x00\xc0\x0c\x20\x53\x65"

class ASFMicroParser(log.Loggable):
    """
    A micro-parser for ASF header objects.
    This implements ONLY the bits that we require here
    """
    
    def __init__(self):
        self.min_pkt_len = 0
        self.max_pkt_len = 0

    def _readType(self, buf, offset):
        guid = buf[offset:offset+16]
        return guid

    def _readUInt64(self, buf, offset):
        return ((ord(buf[offset])) | (ord(buf[offset+1])<<8) | 
                (ord(buf[offset+2])<<16) | (ord(buf[offset+3])<<24) |
                (ord(buf[offset+4])<<32) | (ord(buf[offset+5])<<40) |
                (ord(buf[offset+6])<<48) | (ord(buf[offset+7])<<56))

    def _readUInt32(self, buf, offset):
        return ((ord(buf[offset])) | (ord(buf[offset+1])<<8) | 
                (ord(buf[offset+2])<<16) | (ord(buf[offset+3])<<24))

    def _readUInt16(self, buf, offset):
        return ((ord(buf[offset])) | (ord(buf[offset+1])<<8))

    def _readUInt8(self, buf, offset):
        return ord(buf[offset])

    def _readObject(self, buf, offset):
        """
        Read an object from the buffer at the given offset
        Returns (GUID, offset, length) where offset and length refer to the
        contents of the object (not including the 24 byte object header)
        """
        if len(buf) < 24 + offset:
            self.warning("Invalid object: too short")
            return (GUID.ASF_UNKNOWN, None)
        type = self._readType(buf, offset+0)
        length = self._readUInt64(buf, offset+16)
        if offset + length > len(buf):
            self.warning("Invalid object: too little data available")
        return (type, offset+24, length-24)

    def _parseFilePropertiesObject(self, buf, offset, length):
        self.min_pkt_len = self._readUInt32(buf, offset + 68)
        self.max_pkt_len = self._readUInt32(buf, offset + 72)

        self.debug("Min length: %d, Max %d", self.min_pkt_len, self.max_pkt_len)

    def parseHeader(self, buf):
        (type, offset, length) = self._readObject(buf, 0)
        if type != GUID.ASF_HEADER_OBJECT:
            self.warning("Header object does not contain header")
            return
        # TODO: check length
        numHeaders = self._readUInt32(buf, offset)
        offset += 6

        for i in xrange(numHeaders):
            (guid, offset, length) = self._readObject(buf, offset)

            if guid == GUID.ASF_HEADER_FILE_PROPERTIES_OBJECT:
                self._parseFilePropertiesObject(buf, offset, length)
            offset = offset + length

    def getRequiredPacketLength(self, packet):
        if self.min_pkt_len == self.max_pkt_len:
            return self.min_pkt_len
        else:
            # TODO
            return -1

