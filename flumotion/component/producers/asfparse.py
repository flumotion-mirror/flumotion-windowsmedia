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

from flumotion.component.producers import queue

import gst
import gobject

class GUID:
    # Only includes the ones we care about for now...

    ASF_UNKNOWN = "\0" * 16
    ASF_HEADER_OBJECT = \
        "\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c"
    ASF_HEADER_FILE_PROPERTIES_OBJECT = \
        "\xa1\xdc\xab\x8c\x47\xa9\xcf\x11\x8e\xe4\x00\xc0\x0c\x20\x53\x65"

class ASFHTTPParser(log.Loggable):
    """
    A micro-parser for ASF objects in the HTTP-encapulated format.
    This implements ONLY the bits that we require here
    """
    STATE_HEADER = 0
    STATE_DATA = 1

    HEADER_BYTES = 4

    PACKET_HEADER = 1
    PACKET_DATA = 2
    
    def __init__(self):
        self._http_parser_state = self.STATE_HEADER
        self._bytes_remaining = self.HEADER_BYTES
        self._packet = ""
        self._packet_type = None
        self._asfpackets = []
        self._caps = None

        self._asf_min_pkt_len = 0
        self._asf_max_pkt_len = 0

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
            return (GUID.ASF_UNKNOWN, None, 0)
        type = self._readType(buf, offset+0)
        length = self._readUInt64(buf, offset+16)
        if offset + length > len(buf):
            self.warning("Invalid object: too little data available")
        return (type, offset+24, length-24)

    def _parseFilePropertiesObject(self, buf, offset, length):
        if length != 80:
            self.warning("File properties object has incorrect length")
            return False
        self._asf_min_pkt_len = self._readUInt32(buf, offset + 68)
        self._asf_max_pkt_len = self._readUInt32(buf, offset + 72)

        self.debug("Min length: %d, Max %d", self._asf_min_pkt_len, 
            self._asf_max_pkt_len)
        return True

    def _parseHeaderPacket(self, buf):
        (type, offset, headersLength) = self._readObject(buf, 0)
        if type != GUID.ASF_HEADER_OBJECT:
            self.warning("Header object does not contain header")
            return False
        if headersLength < 6:
            self.warning("ASF Header too short to read")
            return False

        numHeaders = self._readUInt32(buf, offset)
        offset += 6

        for _ in xrange(numHeaders):
            (guid, offset, length) = self._readObject(buf, offset)

            if guid == GUID.ASF_HEADER_FILE_PROPERTIES_OBJECT:
                if not self._parseFilePropertiesObject(buf, offset, length):
                    self.warning("Failed to parse file properties")
                    return False
            offset = offset + length

        headerBuf = gst.Buffer(buf)
            
        self._caps = gst.caps_from_string("video/x-ms-asf")
        # streamheader needs to be a GST_TYPE_ARRAY, which is represented
        # as a tuple in python. Support for this added in gst-python 0.10.8
        try:
            self._caps[0]['streamheader'] = (headerBuf,)
        except:
            return False

        return True

    def _getPacketLength(self, packet):
        if self._asf_min_pkt_len == self._asf_max_pkt_len:
            return self._asf_min_pkt_len
        else:
            # TODO
            return -1

    def _fixupDataPacket(self, buf):
        packetLen = self._getPacketLength(buf)
        self.debug("packet length %d, required to be %d", len(buf), packetLen)
        if len(buf) < packetLen:
            pad = '\0' * (packetLen - len(buf))
            return buf + pad
        else:
            return buf

    def _getDataPacketTimestamp(self, data):
        return -1 # TODO: Implement me!

    def parseData(self, data):
        length = len(data)
        offset = 0
        self.debug("Received %d byte buffer", length)
        while offset < length:
            rem = length - offset
            bytes = min(rem, self._bytes_remaining)
            self._packet += data[offset:offset+bytes]
            self._bytes_remaining -= bytes
            offset += bytes

            if not self._bytes_remaining:
                if self._http_parser_state == self.STATE_HEADER:
                    self._http_parser_state = self.STATE_DATA

                    if self._packet[0] != '$':
                        # TODO: try and recover somehow...
                        self.warning("Synchronisation error")
                        return
                    if self._packet[1] == 'H':
                        self.debug("Header packet header received")
                        self._packet_type = self.PACKET_HEADER
                    elif self._packet[1] == 'D':
                        self.debug("Data packet header received")
                        self._packet_type = self.PACKET_DATA
                    else:
                        self.warning("Unknown packet type: %s", self._packet[1])
                        return

                    self._bytes_remaining = ((ord(self._packet[3]) << 8) |
                                             (ord(self._packet[2])))
                else:
                    if self._packet_type == self.PACKET_HEADER:
                        self.debug("Received ASF header, length %d", 
                            len(self._packet))
                        self._parseHeaderPacket(self._packet)
                        self._asfpackets.append(self._packet)
                    elif self._packet_type == self.PACKET_DATA:
                        self.debug("Received ASF data, length %d", 
                            len(self._packet))
                        packet = self._fixupDataPacket(self._packet)
                        self._asfpackets.append(packet)

                    self._http_parser_state = self.STATE_HEADER
                    self._bytes_remaining = self.HEADER_BYTES

                self._packet = ""

    def hasPacket(self):
        return len(self._asfpackets) != 0

    def getPacketAsBuffer(self):
        data = self._asfpackets.pop(0)
        buf = gst.Buffer(data)
        buf.caps = self._caps
        buf.timestamp = self._getDataPacketTimestamp(data)

        return buf

# Ideally we'd use PushSrc here, but the gst-python wrapping of that appears to
# not work correctly. So this works fine...
class ASFSrc(gst.BaseSrc):
    __gsttemplates__ = (
        gst.PadTemplate("src",
                        gst.PAD_SRC,
                        gst.PAD_ALWAYS,
                        gst.caps_from_string("video/x-ms-asf")),
        )

    def __init__(self, name):
        gst.BaseSrc.__init__(self)
        self.set_name(name)

        self.queue = queue.AsyncQueue()
        self.asfparser = ASFHTTPParser()

    def do_unlock(self):
        self.queue.unblock()

    def do_create(self, offset, size):
        try:
            buf = self.queue.pop()
        except queue.InterruptedException:
            return (gst.FLOW_WRONG_STATE, None)

        return (gst.FLOW_OK, buf)

    def dataReceived(self, data):
        """
        Receive data from the twisted mainloop.
        Parses it to ASF, then adds to async queue.
        """
        self.asfparser.parseData(data)

        while self.asfparser.hasPacket():
            buf = self.asfparser.getPacketAsBuffer()

            self.queue.push(buf)

gobject.type_register(ASFSrc)

