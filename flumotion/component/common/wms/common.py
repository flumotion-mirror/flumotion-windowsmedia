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

import struct


class BitstreamError(Exception):
    """
    Raised when failing to parse the bitstream,
    but we should try to recover.

    Used by the packet parser and the asf parser.
    """

class InvalidBitstreamError(Exception):
    """
    Raised when the bitstream is invalid and nothing can be done.

    Used by the packet parser and the asf parser.
    """

def read_word(buffer, offset=0):
    """Reads a little-endian 16 bits unsigned integer from a  buffer"""
    return struct.unpack("<H", buffer[offset:offset+2])

def read_dword(buffer, offset=0):
    """Reads a little-endian 32 bits unsigned integer from a  buffer"""
    return struct.unpack("<L", buffer[offset:offset+4])

def read_qword(buffer, offset=0):
    """Reads a little-endian 64 bits unsigned integer from a  buffer"""
    return struct.unpack("<Q", buffer[offset:offset+8])

def read_guid(buffer, offset=0):
    """Reads a 128 bits byte string"""
    return buffer[offset:offset+16]
