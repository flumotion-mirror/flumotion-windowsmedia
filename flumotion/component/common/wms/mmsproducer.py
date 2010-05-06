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

from flumotion.common import log

from flumotion.component.common.wms import common

MAX_SIZE = 65535
MAX_LOCID = 4294967295
MAX_INCID = 254 # Not 255 ! ! !

class MMSProducer(object, log.Loggable):

    logCategory = "wms-mmsprod"

    def __init__(self):
        self.reset()

    def register(self, sink):
        self._sink = sink

    def reset(self):
        self.wait_keyframe = True
        self._header = None
        self._h_locid = 0
        self._d_locid = 0
        self._d_incid = 0

    def stop(self):
        eos = self.mms_eos(0)
        if self._sink:
            self._sink.pushData(self, eos)

    def pushHeaders(self, header_obj, data_obj):
        data = header_obj.data + data_obj.data

        if self._header == data:
            return

        self._header_obj = header_obj

        header = self.mms_header(data)

        if self._header is not None:
            eos = self.mms_eos(1)
            change = self.mms_change()

            if self._sink:
                self._sink.pushData(self, eos)
                self._sink.pushData(self, change)

            self.reset()

        self._header = data

        if self._sink:
            self._sink.pushData(self, header)

        self._h_locid = (self._h_locid + 1) % (MAX_LOCID + 1)

        return header

    def pushPacket(self, packet):
        assert self._header is not None, "No header yet"

        if self.wait_keyframe:
            info = self._header_obj.stream_index[packet.stream_number]
            if not (info.is_video and packet.is_keyframe):
                return

        self.wait_keyframe = False

        packet = self.mms_data(packet.data)

        self._d_incid = (self._d_incid + 1) % (MAX_INCID + 1)
        self._d_locid = (self._d_locid + 1) % (MAX_LOCID + 1)

        if self._sink:
            self._sink.pushData(self, packet)

    def mms_header(self, data):
        size = len(data) + 8
        assert size <= MAX_SIZE, "ASF header too big to fit in one MMS packet"

        packet = ["$H",
                  common.encode_word(size),
                  common.encode_dword(self._h_locid),
                  common.encode_byte(0),
                  common.encode_byte(12),
                  common.encode_word(size),
                  data]
        return "".join(packet)


    def mms_data(self, data):
        size = len(data) + 8
        assert size <= MAX_SIZE, "ASF packet too big to fit in one MMS packet"

        packet = ["$D",
                  common.encode_word(size),
                  common.encode_dword(self._d_locid),
                  common.encode_byte(0),
                  common.encode_byte(self._d_incid),
                  common.encode_word(size),
                  data]
        return "".join(packet)

    def mms_eos(self, hresult):
        packet = ["$E",
                  common.encode_word(8),
                  common.encode_dword(hresult)]
        return "".join(packet)

    def mms_change(self):
        packet = ["$C",
                  common.encode_word(4),
                  common.encode_dword(0)]
        return "".join(packet)
