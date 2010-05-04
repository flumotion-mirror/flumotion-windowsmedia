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


class MMSProducer(object, log.Loggable):

    logCategory = "wms-mmsprod"

    def __init__(self, sink):
        self._sink = sink

        self._header = None
        self._locid = 0
        self._incid = 0

    def stop(self):
        pass

    def reset(self):
        self._header = None
        self._locid = 0
        self._incid = 0

    def pushHeader(self, data):
        header = self._mms_header(data)
        if self._header is not None:
            switch = self._mms_switch()
            self._sink.pushData(self, switch)
            self.reset()
        self._header = header
        self._sink.setHeader(self, header)
        self._sink.pushData(self, header)

    def pushPacket(self, data):
        assert self._header is not None, "No header yet"
        packet = self._mms_data(data)
        self._sink.pushData(self, packet)

    def _mms_header(self, data):
        size = len(data) + 8
        packet = ["$H",
                  common.encode_word(size),
                  common.encode_dword(self._locid),
                  common.encode_byte(0),
                  common.encode_byte(12),
                  common.encode_word(size),
                  data]
        self._locid = (self._locid + 1) % 65535
        return "".join(packet)

    def _mms_data(self, data):
        size = len(data) + 8
        packet = ["$D",
                  common.encode_word(size),
                  common.encode_dword(self._locid),
                  common.encode_byte(0),
                  common.encode_byte(self._incid),
                  common.encode_word(size),
                  data]
        self._incid = (self._incid + 1) % 256
        self._locid = (self._locid + 1) % 65535
        return "".join(packet)

    def _mms_switch(self):
        packet = ["$S",
                  common.encode_word(4),
                  common.encode_dword(0)]
        return "".join(packet)
