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

from flumotion.component.producers.wms import queue

import gst
import gobject

class GUID:
    # Only includes the ones we care about for now...

    ASF_UNKNOWN = "\0" * 16
    ASF_HEADER_OBJECT = \
        "\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c"
    ASF_HEADER_FILE_PROPERTIES_OBJECT = \
        "\xa1\xdc\xab\x8c\x47\xa9\xcf\x11\x8e\xe4\x00\xc0\x0c\x20\x53\x65"
    ASF_HEADER_STREAM_PROPERTIES_OBJECT = \
        "\x91\x07\xdc\xb7\xb7\xa9\xcf\x11\x8e\xe6\x00\xc0\x0c\x20\x53\x65"
    ASF_HEADER_EXTENSION_OBJECT = \
        "\xb5\x03\xbf\x5f\x2e\xa9\xcf\x11\x8e\xe3\x00\xc0\x0c\x20\x53\x65"
    ASF_EXTENDED_STREAM_PROPERTIES_OBJECT = \
        "\xcb\xa5\xe6\x14\x72\xc6\x32\x43\x83\x99\xa9\x69\x52\x06\x5b\x5a"

def _GUIDtoString(str):
    # Use the format that fluasfguids.c uses for easier comparison...
    str = "0x%.2x%.2x%.2x%.2x,0x%.2x%.2x,0x%.2x%.2x," \
          "0x%.2x,0x%.2x,0x%.2x,0x%.2x,0x%.2x,0x%.2x,0x%.2x,0x%.2x" % (
          ord(str[3]), ord(str[2]), ord(str[1]), ord(str[0]),
          ord(str[5]), ord(str[4]), ord(str[7]), ord(str[6]),
          ord(str[8]), ord(str[9]), ord(str[10]), ord(str[11]),
          ord(str[12]), ord(str[13]), ord(str[14]), ord(str[15]))
    return str

class InvalidBitstreamException(Exception):
    """
    The bitstream was not parseable
    """

def _readUInt64(buf, offset):
    return ((ord(buf[offset])) | (ord(buf[offset+1])<<8) | 
            (ord(buf[offset+2])<<16) | (ord(buf[offset+3])<<24) |
            (ord(buf[offset+4])<<32) | (ord(buf[offset+5])<<40) |
            (ord(buf[offset+6])<<48) | (ord(buf[offset+7])<<56))

def _readUInt32(buf, offset):
    return ((ord(buf[offset])) | (ord(buf[offset+1])<<8) | 
            (ord(buf[offset+2])<<16) | (ord(buf[offset+3])<<24))

def _readUInt16(buf, offset):
    return ((ord(buf[offset])) | (ord(buf[offset+1])<<8))

def _readUInt8(buf, offset):
    return ord(buf[offset])

class ASFPacketParser(log.Loggable):

    def __init__(self, asfinfo):
        self._asfinfo = asfinfo
        self._data = None
        self._off = 0

        self.packetLen = 0
        self.timestampMS = 0
        self.durationMS = 0
        self.streamNum = 0
        self.hasKeyframe = False

    def readUInt8(self):
        ret = _readUInt8(self._data, self._off)
        self._off += 1
        return ret

    def readUInt16(self):
        ret = _readUInt16(self._data, self._off)
        self._off += 2
        return ret

    def readUInt32(self):
        ret = _readUInt32(self._data, self._off)
        self._off += 4
        return ret

    def readLength(self, lengthtype):
        """
        Read a length from the packet at the given offset.
        The type of the length is given by the 2-bit field 'lengthtype'
        """
        if lengthtype == 0:
            return 0
        elif lengthtype == 1:
            return self.readUInt8()
        elif lengthtype == 2:
            return self.readUInt16()
        elif lengthtype == 3:
            return self.readUInt32()
        else:
            raise InvalidBitstreamException("Invalid length type")

    def parseDataPacket(self, data, offset):
        self._data = data
        self._off = offset

        self.log("offset %d at start", self._off)
        lengthflags = self.readUInt8()
        if lengthflags & 0x80:
            # Has ECC data; we don't check this
            if lengthflags & 0x70:
                # Reserved fields that must be zero
                raise InvalidBitstreamException(
                    "Data packet has ECC length or opaque data present set to "
                    "non-zero")
            ecclen = lengthflags & 0x0f
            self._off += ecclen
            lengthflags = self.readUInt8()
        propertyflags = self.readUInt8()

        self.log("offset %d after reading propertyflags", self._off)
        self._replicateddatalengthtype = propertyflags & 0x03
        self._offsetintomediaobjectlengthtype = (propertyflags & 0x0c) >> 2
        self._mediaobjectnumberlengthtype = (propertyflags & 0x30) >> 4
        streamnumberlengthtype = (propertyflags & 0xc0) >> 6
        if streamnumberlengthtype != 1:
            raise InvalidBitstreamException("Stream number length type "
                "invalid: %d" % streamnumberlengthtype)

        multipay = lengthflags & 0x01

        packetlen = self.readLength((lengthflags & 0x06) >> 1)
        self.readLength((lengthflags & 0x18) >> 3) # sequence
        self.readLength((lengthflags & 0x60) >> 5) # padlen

        # Override for the fixed-size packet case or when packetlen invalid
        if self._asfinfo.min_pkt_len == self._asfinfo.max_pkt_len or \
                packetlen < self._asfinfo.min_pkt_len:
            packetlen = self._asfinfo.min_pkt_len
        elif packetlen > self._asfinfo.max_pkt_len:
            packetlen = self._asfinfo.max_pkt_len
        self.packetLen = packetlen

        self.timestampMS = self.readUInt32()
        self.durationMS = self.readUInt16()
        self.log("Timestamp %dms, duration: %dms", self.timestampMS, self.durationMS)

        # Now we need to actually parse the payloads to figure out whether we
        # have a keyframe... 
        if multipay:
            self.readMultipay()
        else:
            self.readPayload(False)

    def readPayload(self, hasPayloadLength):
        streamNumberByte = self.readUInt8()
        mediaObjectNumber = self.readLength(self._mediaobjectnumberlengthtype)
        # For the compressed payload case, this is actually 'presentation time',
        # but we don't use it, nor verify its validity.
        offsetIntoMediaObject = self.readLength(
            self._offsetintomediaobjectlengthtype)
        self.log("Media object number %d, offset %d", mediaObjectNumber,
            offsetIntoMediaObject)
        replicateddatalength = self.readLength(self._replicateddatalengthtype)
        if replicateddatalength == 1:
            # Special value meaning we have compressed payloads
            self.readUInt8() # prestimedelta
        else:
            # Skip over the replicated data (application or implementation 
            # specific data attached to each payload)
            self._off += replicateddatalength 
        
        streamNumber = streamNumberByte & 0x7f
        if streamNumber not in self._asfinfo.streams:
            raise InvalidBitstreamException(
                "Stream number %d unknown" % streamNumber)

        # Mark as keyframe if any payload within this packet is a keyframe.
        # A payload is considered a keyframe if it's the first payload for this
        # media object (i.e. has offset into media object set to 0)
        kf = (streamNumberByte & 0x80) and offsetIntoMediaObject == 0

        self.hasKeyframe = self.hasKeyframe or bool(kf)
        self.log("Payload hasKeyframe: %r", self.hasKeyframe)

        self._asfinfo.streams[streamNumber].prevMediaObjectNumber = \
            mediaObjectNumber

        if hasPayloadLength:
            payloadLength = self.readLength(self._payloadlengthtype)
            self._off += payloadLength

    def readMultipay(self):
        payloadflags = self.readUInt8()
        numPayloads = payloadflags & 0x3f
        self._payloadlengthtype = (payloadflags & 0xc0) >> 6

        for _ in xrange(numPayloads):
            self.readPayload(True)

class ASFStreamInfo(object):
    def __init__(self):
        # We don't yet actually read any per-stream information out
        pass

class ASFInfo(object):
    def __init__(self):
        self.min_pkt_len = 0
        self.max_pkt_len = 0
        self.hasKeyframes = False

        self.streams = {} # streamNumber -> ASFStreamInfo

class ASFHTTPParser(log.Loggable):
    """
    A micro-parser for ASF objects in the HTTP-encapulated format.
    This implements ONLY the bits that we require here
    """
    STATE_HEADER = 0
    STATE_DATA = 1

    HEADER_BYTES = 4

    PACKET_UNKNOWN = 0
    PACKET_HEADER = 1
    PACKET_DATA = 2
    PACKET_EOS = 3
    PACKET_CLEAR = 4
    PACKET_FILLER = 5

    def __init__(self, push):
        # There are two variants on the format. One is used in push mode, one in
        # pull mode. The only difference I've noted is that each packet in 
        # pull mode has a 12 byte header, push mode is 4 bytes. The 12 byte 
        # header starts with the same 4 bytes, then has an extra 8 bytes that 
        # I don't know the meaning of (but ignoring them seems to work ok)
        self._pushmode = push
        self.debug("Initialised in %s", push and "push" or "pull")
        self.reset()

    def reset(self):
        self._http_parser_state = self.STATE_HEADER
        self._bytes_remaining = self.HEADER_BYTES
        self._packet = ""
        self._packet_type = None
        self._asfbuffers = []
        self._caps = None
        self._asfinfo = ASFInfo()

    def _readType(self, buf, offset):
        guid = buf[offset:offset+16]
        return guid

    def _readObject(self, buf, offset):
        """
        Read an object from the buffer at the given offset
        Returns (GUID, offset, length) where offset and length refer to the
        contents of the object (not including the 24 byte object header)
        """
        if len(buf) < 24 + offset:
            raise InvalidBitstreamException("Invalid object: too short")
        type = self._readType(buf, offset+0)
        length = _readUInt64(buf, offset+16)
        if offset + length > len(buf):
            raise InvalidBitstreamException("Invalid object: data too short")
        return (type, offset+24, length-24)

    def _parseStreamPropertiesObject(self, buf, offset, length):
        if length < 54:
            raise InvalidBitstreamException(
                "Stream properties object too short")

        flags = _readUInt16(buf, offset + 48)
        streamNumber = flags & 0x7f

        self.debug("Parsed stream properties object for stream %d", 
            streamNumber)
        self._asfinfo.streams[streamNumber] = ASFStreamInfo()

    def _parseFilePropertiesObject(self, buf, offset, length):
        if length != 80:
            raise InvalidBitstreamException(
                "File properties object has incorrect length")
        self._asfinfo.min_pkt_len = _readUInt32(buf, offset + 68)
        self._asfinfo.max_pkt_len = _readUInt32(buf, offset + 72)

        self.debug("Min length: %d, Max %d", self._asfinfo.min_pkt_len, 
            self._asfinfo.max_pkt_len)

    def _parseExtendedStreamPropertiesObject(self, buf, offset, length):
        # Right now, we only care about the flags, which we know the offset of 
        if length < 64:
            raise InvalidBitstreamException("ExtendedStreamPropertiesObject too"
                "short to read")
        self.debug("Parsing ExtendedStreamPropertiesObject")

        flags = _readUInt32(buf, offset+44)
        streamNumber = _readUInt16(buf, offset+48)
        nocleanpointflag = flags & 0x04
        self._asfinfo.hasKeyframes = self._asfinfo.hasKeyframes or \
            (not nocleanpointflag)
        self.debug("Parsed nocleanpoint flag: hasKeyframes now %r", 
            self._asfinfo.hasKeyframes)

        self.debug("Parsed extended stream properties object for stream %d", 
            streamNumber)
        if streamNumber not in self._asfinfo.streams:
            self._asfinfo.streams[streamNumber] = ASFStreamInfo()

    def _parseHeaderExtensionObject(self, buf, offset, length):
        if length < 22:
            raise InvalidBitstreamException("Header extension object too short")
        self.debug("Parsing header extension object")
        end = offset + length
        #dataSize = _readUInt32(buf, offset + 18)
        offset += 22

        while offset < end:
            (guid, offset, length) = self._readObject(buf, offset)
            self.debug("Parsing an extension header: %s", _GUIDtoString(guid))
            if guid == GUID.ASF_EXTENDED_STREAM_PROPERTIES_OBJECT:
                self._parseExtendedStreamPropertiesObject(buf, offset, length)
                
            offset = offset + length

    def _getHeaderBuffer(self, buf):
        if self._pushmode:
            (type, offset, headersLength) = self._readObject(buf, 0)
        else:
            (type, offset, headersLength) = self._readObject(buf, 8)

        if type != GUID.ASF_HEADER_OBJECT:
            raise InvalidBitstreamException(
                "Header object does not contain ASF header")
        if headersLength < 6:
            raise InvalidBitstreamException("ASF header too short to read")

        numHeaders = _readUInt32(buf, offset)
        offset += 6

        for _ in xrange(numHeaders):
            (guid, offset, length) = self._readObject(buf, offset)

            if guid == GUID.ASF_HEADER_FILE_PROPERTIES_OBJECT:
                self._parseFilePropertiesObject(buf, offset, length)
            elif guid == GUID.ASF_HEADER_STREAM_PROPERTIES_OBJECT:
                self._parseStreamPropertiesObject(buf, offset, length)
            elif guid == GUID.ASF_HEADER_EXTENSION_OBJECT:
                self._parseHeaderExtensionObject(buf, offset, length)
            else:
                self.debug("Unrecognised top-level header: %s", _GUIDtoString(guid))
            offset = offset + length

        if self._pushmode:
            headerBuf = gst.Buffer(buf)
        else:
            headerBuf = gst.Buffer(buf[8:])

        headerBuf.timestamp = gst.CLOCK_TIME_NONE
        headerBuf.duration = gst.CLOCK_TIME_NONE
        headerBuf.flag_set(gst.BUFFER_FLAG_IN_CAPS)
            
        self._caps = gst.caps_from_string("video/x-ms-asf")
        # streamheader needs to be a GST_TYPE_ARRAY, which is represented
        # as a tuple in python. Support for this added in gst-python 0.10.8
        try:
            self._caps[0]['streamheader'] = (headerBuf,)
        except:
            return False

        headerBuf.caps = self._caps

        return headerBuf

    def _getDataBuffer(self, data):
        pp = ASFPacketParser(self._asfinfo)
        if self._pushmode:
            pp.parseDataPacket(data, 0)
        else:
            pp.parseDataPacket(data, 8)

        # Some of these require padding to be added here.
        self.log("packet length %d, required to be %d", len(data), 
            pp.packetLen)
        if len(data) < pp.packetLen:
            pad = '\0' * (pp.packetLen - len(data))
            data = data + pad

        if self._pushmode:
            buf = gst.Buffer(data)
        else:
            buf = gst.Buffer(data[8:])
        buf.caps = self._caps

        buf.timestamp = pp.timestampMS * gst.MSECOND
        buf.duration = pp.durationMS * gst.MSECOND
        if self._asfinfo.hasKeyframes and not pp.hasKeyframe:
            self.log("Setting delta unit")
            buf.flag_set(gst.BUFFER_FLAG_DELTA_UNIT)
        else:
            self.log("Not setting delta unit")

        return buf

    def parseData(self, data):
        ret = True
        length = len(data)
        offset = 0
        self.log("Received %d byte buffer", length)
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
                        raise InvalidBitstreamException(
                            "Packet does not start with '$' packet-start "
                            "indicator, synchronisation lost")
                    if self._packet[1] == 'H':
                        self.debug("Header packet header received")
                        self._packet_type = self.PACKET_HEADER
                    elif self._packet[1] == 'C':
                        self.debug("Clear packet header received")
                        self._packet_type = self.PACKET_CLEAR
                    elif self._packet[1] == 'D':
                        self.log("Data packet header received")
                        self._packet_type = self.PACKET_DATA
                    elif self._packet[1] == 'E':
                        # We don't parse the contents of this packet currently;
                        # I haven't even looked to see what it contains
                        self.info("EOS packet received, halting")
                        self._packet_type = self.PACKET_EOS
                    elif self._packet[1] == 'F':
                        # Filler packet received; ignore it.
                        self._packet_type = self.PACKET_FILLER
                    else:
                        # We'll just skip over this one...
                        self.warning("Unknown packet type: %s", self._packet[1])
                        self._packet_type = self.PACKET_UNKNOWN

                    self._bytes_remaining = ((ord(self._packet[3]) << 8) |
                                             (ord(self._packet[2])))
                else:
                    if self._packet_type == self.PACKET_HEADER:
                        self.debug("Received ASF header, length %d", 
                            len(self._packet))
                        buf = self._getHeaderBuffer(self._packet)
                        self.debug("Appending header buffer of length %d",
                            len(buf))
                        self.debug("Buf starts with %x, %x, %x", ord(buf[0]), ord(buf[1]), ord(buf[2]))
                        self._asfbuffers.append(buf)
                    elif self._packet_type == self.PACKET_DATA:
                        self.log("Received ASF data, length %d", 
                            len(self._packet))
                        buf = self._getDataBuffer(self._packet)
                        self._asfbuffers.append(buf)
                    elif self._packet_type == self.PACKET_FILLER:
                        self.debug("Dropping filler packet")
                    elif self._packet_type == self.PACKET_EOS:
                        self.debug("Received EOS")
                        ret = False
                    elif self._packet_type == self.PACKET_CLEAR:
                        self.debug("Clear packet? Not sure what to do...")
                    elif self._packet_type == self.PACKET_UNKNOWN:
                        # Write out up to 20 bytes for later perusal...
                        outlen = min(len(self._packet), 20)
                        p = ""
                        for i in xrange(outlen):
                            p += "0x%.2x," % (ord(self._packet[i]),)
                        self.debug("Unknown packet: %s", p)

                    self._http_parser_state = self.STATE_HEADER
                    self._bytes_remaining = self.HEADER_BYTES

                self._packet = ""
        return ret

    def hasBuffer(self):
        return len(self._asfbuffers) != 0

    def getBuffer(self):
        return self._asfbuffers.pop(0)

# Ideally we'd use PushSrc here, but the gst-python wrapping of that appears to
# not work correctly. So this works fine...
class ASFSrc(gst.BaseSrc):
    __gsttemplates__ = (
        gst.PadTemplate("src",
                        gst.PAD_SRC,
                        gst.PAD_ALWAYS,
                        gst.caps_from_string("video/x-ms-asf")),
        )

    def __init__(self, name, push=True):
        gst.BaseSrc.__init__(self)
        self.set_name(name)

        self.queue = queue.AsyncQueue()
        self.asfparser = ASFHTTPParser(push)

    def resetASFParser(self):
        self.asfparser.reset()

    def do_unlock(self):
        self.queue.unblock()

    def do_create(self, offset, size):
        try:
            (flowreturn, buf) = self.queue.pop()
        except queue.InterruptedException:
            return (gst.FLOW_WRONG_STATE, None)

        return (flowreturn, buf)

    def dataReceived(self, data):
        """
        Receive data from the twisted mainloop.
        Parses it to ASF, then adds to async queue.
        """
        if not self.asfparser.parseData(data):
            # EOS; but don't send an EOS, so we can recover if an encoder 
            # reconnects later
            return

        while self.asfparser.hasBuffer():
            buf = self.asfparser.getBuffer()

            self.queue.push((gst.FLOW_OK, buf))

gobject.type_register(ASFSrc)
