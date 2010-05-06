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

import re

from flumotion.common import log

from flumotion.component.common.wms import common

LOG_CATEGORY = "wms-asfparser"

FLAG_KEYFRAME = range(1)

GUID_FORMAT = "-".join(["%.2X"*4, "%.2X"*2, "%.2X"*2, "%.2X"*2, "%.2X"*6])
GUID_REGEX = re.compile("([0123456789ABCDEFabcfed]{8})-"
                        "([0123456789ABCDEFabcfed]{4})-"
                        "([0123456789ABCDEFabcfed]{4})-"
                        "([0123456789ABCDEFabcfed]{4})-"
                        "([0123456789ABCDEFabcfed]{12})")


def guid2str(guid):
    """
    Return a string representation of a GUID using the same format
    as the ASF specification: 5 parts separated by dashes:

      - 32 bits little endian
      - 16 bits little endian
      - 16 bits little endian
      - 16 bits big endian
      - 48 bits big endian

    Ex:
        "\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c"
      =>
        "3026B275-8E66-CF11-A6D9-00AA0062CE6C"
    """
    bytes = [ord(c) for c in guid]
    reordered = bytes[3::-1] + bytes[5:3:-1] + bytes[7:5:-1] + bytes[8:]
    return GUID_FORMAT % tuple(reordered)

def str2guid(text):
    """
    Decode the string representation of a GUID as used in ASF
    specification. See gui2str for more details.
    """
    match = GUID_REGEX.match(text)
    if not match: return None
    binary =  ''.join([s.decode("hex") for s in match.groups()])
    return binary[3::-1] + binary[5:3:-1] + binary[7:5:-1] + binary[8:]


class GUID:

    ASF_HEADER_OBJECT = \
        str2guid("75B22630-668E-11CF-A6D9-00AA0062CE6C")

    ASF_DATA_OBJECT = \
        str2guid("75B22636-668E-11CF-A6D9-00AA0062CE6C")

    ASF_HEADER_FILE_PROPERTIES_OBJECT = \
        str2guid("8CABDCA1-A947-11CF-8EE4-00C00C205365")

    ASF_HEADER_STREAM_PROPERTIES_OBJECT = \
        str2guid("B7DC0791-A9B7-11CF-8EE6-00C00C205365")

    ASF_BITRATE_MUTUAL_EXCLUSION_OBJECT = \
        str2guid("D6E229DC-35DA-11D1-9034-00A0C90349BE")

    ASF_STREAM_BITRATE_PROPERTIES_OBJECT = \
        str2guid("7BF875CE-468D-11D1-8D82-006097C9A2B2")

    ASF_HEADER_EXTENSION_OBJECT = \
        str2guid("5FBF03B5-A92E-11CF-8EE3-00C00C205365")

    ASF_EXTENDED_STREAM_PROPERTIES_OBJECT = \
        str2guid("14E6A5CB-C672-4332-8399-A96952065B5A")

    ASF_AUDIO_MEDIA = \
        str2guid("F8699E40-5B4D-11CF-A8FD-00805F5C442B")

    ASF_VIDEO_MEDIA = \
        str2guid("BC19EFC0-5B4D-11CF-A8FD-00805F5C442B")


class ParserCallbacks(object):

    def pushHeaders(self, parser, header_obj, data_obj):
        pass

    def pushPacket(self, parser, packet):
        pass


class NeedMoreData(Exception):

    def __init__(self, needed):
        self.needed = needed


class ASFParser(object, log.Loggable):

    logCategory = LOG_CATEGORY

    def __init__(self, callbacks):
        self.debug("ASF parser initialized")

        self.has_keyframe = False

        self._parsers = {GUID.ASF_HEADER_OBJECT: self._parse_header_object,
                         GUID.ASF_DATA_OBJECT:   self._parse_data_object}
        self._callbacks = callbacks
        self._data = ""
        self._header_obj = None
        self._info_obj = None

        self.reset()

    def reset(self):
        self.debug("ASF parser reset")
        self._header_obj = None
        self._data_obj = None
        self.has_keyframe = False

    def pushData(self, data):
        self._data += data

        buffer = self._data
        offset = 0
        checkpoint = 0

        try:

            while not (self._header_obj and self._data_obj):
                offset = self._parse_top_level_object(buffer, offset)
                checkpoint = offset

            while True:
                offset = self._parse_packet(buffer, offset)
                checkpoint = offset

        except NeedMoreData, e:
            self.log("Need at least %d more bytes", e.needed)

        if checkpoint:
            self._data = self._data[checkpoint:]

    def _check_available(self, buffer, offset, size):
        if len(buffer) - offset < size:
            raise NeedMoreData(size - len(buffer) + offset )

    def _parse_top_level_object(self, buffer, offset):
        self._check_available(buffer, offset, 24)

        guid = common.read_guid(buffer, offset)
        size = common.read_qword(buffer, offset + 16)

        parser = self._parsers.get(guid)
        if not parser:
            msg = "Unknown top-level ASF object %s" % guid2str(guid)
            if len(buffer) < 200000:
                raise NeedMoreData(42)
            else:
                raise common.InvalidBitstreamError(msg)

        self._check_available(buffer, offset, size)

        return parser(buffer, offset)

    def _parse_header_object(self, buffer, offset):
        if self._header_obj is not None:
            self.reset()

        parser = ASFHeader()
        offset = parser.parse(buffer, offset)

        self._header_obj = parser

        return offset

    def _parse_data_object(self, buffer, offset):
        if self._header_obj is None:
            msg = "ASF data without a valid ASF header"
            raise common.InvalidBitstreamError(msg)

        if self._data_obj is not None:
            msg = "Duplicated ASF data object"
            raise common.InvalidBitstreamError(msg)

        parser = ASFData(self._header_obj)
        offset = parser.parse(buffer, offset)

        self._data_obj = parser

        self._callbacks.pushHeaders(self, self._header_obj, parser)

        return offset

    def _parse_packet(self, buffer, offset):
        if self._header_obj is None:
            msg = "ASF packet without a valid ASF header"
            raise common.InvalidBitstreamError(msg)

        if self._data_obj is None:
            msg = "ASF packet without a valid data object"
            raise common.InvalidBitstreamError(msg)

        parser = ASFPacket(self._header_obj)
        offset = parser.parse(buffer, offset)

        self.has_keyframe = self.has_keyframe or parser.is_keyframe

        self._callbacks.pushPacket(self, parser)

        return offset


class ASFStreamInfo(object):

    def __init__(self, number):
        self.number = number
        self.stream_type = None
        self.time_offset = None
        self.start_time = None
        self.end_time = None
        self.bitrate = None
        self.needs_reliable = True
        self.has_cleanpoints = False
        self.is_audio = False
        self.is_video = False


class ASFElement(object, log.Loggable):

    logCategory = LOG_CATEGORY

    def __init__(self):
        self.data = None

    def parse(self, buffer, offset):
        raise NotImplementedError()

    def _check_available(self, buffer, offset, size):
        if len(buffer) - offset < size:
            raise NeedMoreData(size - len(buffer) + offset )


class ASFObject(ASFElement):

    def __init__(self):
        ASFElement.__init__(self)

    def _parse_object(self, buffer, offset, parsers):
        guid, size, _ = self._parse_object_header(buffer, offset)

        parser = parsers.get(guid)

        if parser:
            parser(buffer, offset, size)
        else:
            self.debug("Unrecognized ASF object: %s",
                       guid2str(guid))

        return offset + size

    def _parse_object_header(self, buffer, offset):
        """Parses and check ASF objects header."""
        self._check_available(buffer, offset, 24)

        guid = common.read_guid(buffer, offset)
        size = common.read_qword(buffer, offset+16)

        self._check_available(buffer, offset, size)

        return guid, size, offset + 16 + 8


class ASFHeader(ASFObject):

    BROADCAST_FLAG_MASK = 0x00000001
    STREAM_NUMBER_MASK = 0x007f
    RELIABLE_MASK = 0x00000001
    NO_CLEANPOINTS_MASK = 0x00000004
    RESEND_LIVE_CLEANPOINTS_MASK = 0x00000008

    def __init__(self):
        ASFObject.__init__(self)
        self._parsers = {GUID.ASF_HEADER_FILE_PROPERTIES_OBJECT:
                            self._parse_file_properties,
                         GUID.ASF_HEADER_STREAM_PROPERTIES_OBJECT:
                            self._parse_stream_properties,
                         GUID.ASF_HEADER_EXTENSION_OBJECT:
                            self._parse_header_extension,
                         GUID.ASF_BITRATE_MUTUAL_EXCLUSION_OBJECT:
                            self._parse_bitrate_exclusion,
                         GUID.ASF_STREAM_BITRATE_PROPERTIES_OBJECT:
                            self._parse_bitrate_properties,
                         GUID.ASF_EXTENDED_STREAM_PROPERTIES_OBJECT:
                            self._parse_extended_stream_properties}

        self.fid = None
        self.is_broadcast = None
        self.min_packet_size = None
        self.max_packet_size = None
        self.stream_index = {} # {STREAM_NUMBER: ASFStreamInfo}

    def parse(self, buffer, offset):
        """
        Parse ASF top-level headers, starting by the mandatory "Header Object".

        See ASF specs 3.1
        """

        self.debug("Parsing ASF header object...")
        self._check_available(buffer, offset, 30)

        start_offset = offset
        guid, size, offset = self._parse_object_header(buffer, offset)

        if guid != GUID.ASF_HEADER_OBJECT:
            msg = "Header packet does not contain ASF header object"
            raise common.BitstreamError(msg)

        if size < 30:
            raise common.BitstreamError("ASF header object too short")

        object_count = common.read_dword(buffer, offset)
        offset += 6

        while object_count > 0:
            offset = self._parse_object(buffer, offset, self._parsers)
            object_count -= 1

        self.data = buffer[start_offset:offset]
        return offset

    def _parse_file_properties(self, buffer, offset, size):
        """
        Parses and checks ASF top-level header "File Properties Object".

        See ASF specs  3.2
        """
        self.debug("Parsing File Properties Object")

        if size != 104:
            msg = ("ASF top-level header 'File Properties Object' "
                   "has incorrect size: %d" % size)
            raise common.BitstreamError(msg)

        self.fid = common.read_guid(buffer, offset + 24)

        flags = common.read_dword(buffer, offset + 88)
        self.is_broadcast = (flags & self.BROADCAST_FLAG_MASK) != 0

        self.min_packet_size = common.read_dword(buffer, offset + 92)
        self.max_packet_size = common.read_dword(buffer, offset + 96)

        self.debug("File properties parsed: file ID %s, Broadcast %s, Packet "
                   "Size min/max %d/%d", guid2str(self.fid), self.is_broadcast,
                   self.min_packet_size, self.max_packet_size)

    def _get_stream(self, number):
        if number not in self.stream_index:
            if number > 127:
                msg = "Invalid stream number: %d" % number
                raise common.BitstreamError(msg)
            info = ASFStreamInfo(number)
            self.stream_index[number] = info
            return info
        return self.stream_index[number]

    def _parse_stream_properties(self, buffer, offset, size):
        """
        Parses and checks ASF top-level header "Stream Properties Object".

        See ASF specs  3.3
        """
        self.debug("Parsing Stream Properties Object")

        if size < 78:
            msg = ("ASF top-level header 'Stream Properties Object' "
                   "has incorrect size: %d" % size)
            raise common.BitstreamError(msg)

        flags = common.read_word(buffer, offset + 72)
        number = flags & self.STREAM_NUMBER_MASK

        stream_type = common.read_guid(buffer, offset + 24)
        time_offset = common.read_qword(buffer, offset + 56)

        info = self._get_stream(number)
        info.type = stream_type
        info.time_offset = time_offset

        if stream_type == GUID.ASF_AUDIO_MEDIA:
            info.is_audio = True
            self.debug("Audio stream %d properties parsed", number)
        elif stream_type == GUID.ASF_VIDEO_MEDIA:
            info.is_video = True
            self.debug("Video stream %d properties parsed", number)
        else:
            self.debug("Stream %d properties parsed, unrecognized type %s",
                       number, guid2str(stream_type))

    def _parse_header_extension(self, buffer, offset, size):
        """
        Parses and checks ASF top-level header "Header Extension Object".

        See ASF specs  3.4
        """
        self.debug("Parsing Header Extension Object")

        if size < 46:
            msg = ("ASF top-level header 'Header Extension Object' "
                   "has incorrect size: %d" % size)
            raise common.BitstreamError(msg)

        extra_size = common.read_dword(buffer, offset + 42)
        if size != (extra_size + 46):
            msg = ("ASF top-level header 'Header Extension Object' "
                   "has incorrect extra size: %d" % extra_size)
            raise common.BitstreamError(msg)

        end = offset + size
        offset = offset + 42 + 4
        count = 0
        while offset < end:
            offset = self._parse_object(buffer, offset, self._parsers)
            count += 1

        self.debug("%d header extensions parsed", count)

    def _parse_bitrate_exclusion(self, buffer, offset, size):
        """
        Parses and checks ASF top-level header
        "Bitrate Mutual Exclusion Object".

        See ASF specs  3.8
        """
        self.debug("Parsing Bitrate Mutual Exclusion Object")


    def _parse_bitrate_properties(self, buffer, offset, size):
        """
        Parses and checks ASF top-level header
        "Stream Bitrate Properties Object".

        See ASF specs  3.8
        """
        self.debug("Parsing Stream Bitrate Properties Object")

        if size < 26:
            msg = ("ASF top-level header 'Stream Bitrate Properties Object' "
                   "has incorrect size: %d" % size)
            raise common.BitstreamError(msg)

        count = common.read_word(buffer, offset + 24)
        offset += 26
        for _ in xrange(count):
            flags = common.read_word(buffer, offset)
            number = flags & self.STREAM_NUMBER_MASK
            bitrate = common.read_dword(buffer, offset + 2)

            info = self._get_stream(number)
            info.bitrate = bitrate

            offset += 6

            self.debug("Stream %d bitrate: %d", number, bitrate)

        self.debug("%d stream bitrate properties parsed", count)

    def _parse_extended_stream_properties(self, buffer, offset, size):
        """
        Parses and checks ASF extension header
        "Extended Stream Properties Object".

        See ASF specs  4.1
        """

        self.debug("Parsing Extended Stream Properties Object")

        if size < 88:
            msg = ("ASF extension header 'Extended Stream Properties Object' "
                   "has incorrect size: %d" % size)
            raise common.BitstreamError(msg)

        start_time = common.read_qword(buffer, offset + 24)
        end_time = common.read_qword(buffer, offset + 32)

        flags = common.read_dword(buffer, offset + 68)
        reliable = flags & self.RELIABLE_MASK
        nocleanpoints = flags & self.NO_CLEANPOINTS_MASK
        resendcps = flags & self.RESEND_LIVE_CLEANPOINTS_MASK

        number = common.read_word(buffer, offset + 72)

        frame_duration = common.read_qword(buffer, offset + 76)

        info = self._get_stream(number)
        info.start_time = start_time
        info.end_time = end_time
        info.has_cleanpoints = nocleanpoints == 0
        info.needs_reliable = reliable != 0
        if frame_duration > 0:
            info.frame_duration = frame_duration

        self.debug("Extended properties of stream %d parsed: start time %d, "
                   "end time %d, frame duration %d, needs reliable %s, "
                   "has cleanpoints %s, resend cleanpoints %s"
                   % (number, start_time, end_time, frame_duration,
                      reliable != 0, nocleanpoints == 0, resendcps != 0))


class ASFData(ASFObject):

    def __init__(self, header):
        ASFObject.__init__(self)
        self.header = header

    def parse(self, buffer, offset):
        """
        Parse ASF top-level data object.
        See ASF specs 5.1
        """
        # There is maybe a 8 bytes difference if in pull or push mode (???)
        self.debug("Parsing ASF data object...")
        self._check_available(buffer, offset, 50)

        start_offset = offset
        guid, size, offset = self._parse_object_header(buffer, offset)

        if guid != GUID.ASF_DATA_OBJECT:
            msg = "Data packet does not contain ASF data object"
            raise common.BitstreamError(msg)

        if size < 50:
            if not (self.header.is_broadcast and size == 0):
                raise common.BitstreamError("ASF data object too short")

        fid = common.read_guid(buffer, offset)

        if fid != self.header.fid:
            raise common.BitstreamError("Invalid ASF data object FID")

        # Not reading or checking packet count field

        offset += 26

        self.data = buffer[start_offset:offset]

        return offset


class ASFPacket(ASFElement):

    def __init__(self, header):
        ASFElement.__init__(self)
        self.header = header
        self.timestamp = None
        self.duration = None
        self.packet_length = None
        self.sequence = None
        self.padding_length = None
        self.is_keyframe = False
        self.stream_number = None
        self.time_delta = None
        self.presentation_time = None
        self.data = None

        self.padding_length_type = None
        self.padding_length_offset = None

        self._single_payload = None
        self._replicated_data_length_type = None
        self._offset_into_media_object_length_type = None
        self._media_object_number_length_type = None

    def parse(self, buffer, offset):
        """
        Parse ASF packet.
        See ASF specs 5.2
        """
        self.log("Parsing ASF data packet...")
        start_offset = offset

        offset = self._parse_ecc_data(buffer, offset)
        offset = self._parse_payload_info(buffer, offset)
        offset = self._parse_payload_data(buffer, offset)

        if self.padding_length:
            offset +=  self.padding_length

        self.data = buffer[start_offset:offset]

        return offset


    def _parse_ecc_data(self, buffer, offset):
        """
        Parse ASF data object error correction data.
        See ASF specs 5.2.1
        """
        self._check_available(buffer, offset, 1)

        flags = common.read_byte(buffer, offset)
        if flags & 0x80 == 0:
            # No error correction data
            return offset

        if flags & 0x70:
            # Reserved fields that must be zero
            raise common.BitstreamError("Data packet has ECC length or opaque "
                                        "data present set to non-zero")

        ecclen = flags & 0x0F

        self._check_available(buffer, offset, ecclen)

        return offset + ecclen

    def _parse_payload_info(self, buffer, offset):
        """
        Parse ASF data object payload information.
        See ASF specs 5.2.2
        """
        self._check_available(buffer, offset, 2)

        length_flags = common.read_byte(buffer, offset)
        props_flags = common.read_byte(buffer, offset + 1)

        self._single_payload = (length_flags & 0x01) == 0
        sequence_length_type = (length_flags & 0x06) >> 1
        padding_length_type = (length_flags & 0x18) >> 3
        packet_length_type = (length_flags & 0x60) >> 5

        # For later use in _parse_payload_data
        self._replicated_data_length_type = props_flags & 0x03
        self._offset_into_media_object_length_type = (props_flags & 0x0C) >> 2
        self._media_object_number_length_type = (props_flags & 0x30) >> 4

        stream_number_length_type = (props_flags & 0xC0) >> 6
        if stream_number_length_type != 1:
            raise common.BitstreamError("Stream number length type invalid: %d"
                                        % stream_number_length_type)

        total_size = (2
                      + packet_length_type
                      + sequence_length_type
                      + padding_length_type
                      + 4 + 2)
        self._check_available(buffer, offset, total_size)

        offset = offset + 2
        packet_length, offset = common.read_length(buffer, offset,
                                                   packet_length_type)
        sequence, offset = common.read_length(buffer, offset,
                                              sequence_length_type)
        padding_length_offset = offset
        padding_length, offset = common.read_length(buffer, offset,
                                                    padding_length_type)

        send_time = common.read_dword(buffer, offset)
        duration = common.read_word(buffer, offset + 4)
        offset = offset + 4 + 2

        # Override for the fixed-size packet case or when packetlen invalid
        minlen = self.header.min_packet_size
        maxlen = self.header.max_packet_size
        if minlen == maxlen or packet_length < minlen:
            packet_length = minlen
        elif packet_length > maxlen:
            packet_length = maxlen

        self.packet_length = packet_length

        self.sequence = sequence
        self.padding_length = padding_length
        self.timestamp = send_time
        self.duration = duration

        # To be able to change the padding length
        self.padding_length_type = padding_length_type
        self.padding_length_offset = padding_length_offset

        return offset

    def _parse_payload_data(self, buffer, offset):
        """
        Parse ASF data object payload data.
        See ASF specs 5.2.3
        """
        if self._single_payload:
            return self._parse_single_payload_data(buffer, offset)
        return self._parse_multiple_payload_data(buffer, offset)

    def _parse_multiple_payload_data(self, buffer, offset):
        self._check_available(buffer, offset, 1)
        payload_flags = common.read_byte(buffer, offset)
        offset += 1

        payload_num = payload_flags & 0x3F
        payload_length_type = (payload_flags & 0xC0) >> 6

        while payload_num > 0:
            payload_num -= 1
            offset = self._parse_single_payload_data(buffer, offset,
                                                     payload_length_type)
        return offset

    def _parse_single_payload_data(self, buffer, offset,
                                   payload_length_type=None):
        """
        Parse ASF data object single payload data.
        See ASF specs 5.2.3.1
        """
        header_size = (1
                       + self._media_object_number_length_type
                       + self._offset_into_media_object_length_type
                       + self._replicated_data_length_type
                       + 1)
        self._check_available(buffer, offset, header_size)

        stream_number_byte = common.read_byte(buffer, offset)
        offset += 1
        length_type = self._media_object_number_length_type
        media_object_number, offset = common.read_length(buffer, offset,
                                                         length_type)
        length_type = self._offset_into_media_object_length_type
        offset_into_object, offset = common.read_length(buffer, offset,
                                                        length_type)
        length_type = self._replicated_data_length_type
        replicated_data_length, offset = common.read_length(buffer, offset,
                                                            length_type)

        # What about this one ? not working if I read it...
        #time_delta = common.read_byte(buffer, offset)
        #offset += 1

        self.log("ASF media object number %d, offset %d",
                 media_object_number, offset_into_object)

        stream_number = stream_number_byte & 0x7F
        if stream_number not in self.header.stream_index:
            raise common.BitstreamError("Unknown stream number %d"
                                        % stream_number)

        # Mark as keyframe if any payload within this packet is a keyframe.
        # A payload is considered a keyframe if it's the first payload for this
        # media object (i.e. has offset into media object set to 0)
        keyframe = (stream_number_byte & 0x80) and (offset_into_object == 0)

        if replicated_data_length:
            # 1 is a special value meaning we have compressed payloads
            #if replicated_data_length > 1:
                self._check_available(buffer, offset, replicated_data_length)
                offset += replicated_data_length

        # And the payload for single payload ?????
        # Should try with audio only...

        if payload_length_type is not None:
            # Called from _parse_multiple_payload_data
            self._check_available(buffer, offset, payload_length_type)
            payload_length, offset = common.read_length(buffer, offset,
                                                        payload_length_type)
            if payload_length:
                self._check_available(buffer, offset, payload_length)
                offset += payload_length

        self.is_keyframe = bool(keyframe)
        self.stream_number = stream_number
        #self.time_delta = time_delta
        self.presentation_time = offset_into_object

        return offset

