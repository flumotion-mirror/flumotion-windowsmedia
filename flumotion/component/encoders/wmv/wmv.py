# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006,2007 Fluendo, S.A. (www.fluendo.com).
# All rights reserved.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

from twisted.internet import defer

from flumotion.common import gstreamer
from flumotion.common.format import formatStorage
from flumotion.common.i18n import gettexter, N_
from flumotion.common.messages import Error
from flumotion.component import feedcomponent

T_ = gettexter('flumotion')


class WMVEncoder(feedcomponent.EncoderComponent):
    checkTimestamp = True
    checkOffset = True
    wmvEncoder = None
    wmvVersion = None

    def init(self):
        self.uiState.addKey('encoder', 0)
        self.uiState.addKey('version', 0)
        self.uiState.addKey('bitrate', 0)
        self.uiState.addKey('complexity', 0)

    def do_check(self):
        self.debug('running Windows Media Video encoder check.')

        version = self.config['properties'].get('version', 3)

        # For WMV2 we need dmoenc_wmvdmoe2v2.
        if version == 2:
            if gstreamer.element_factory_exists('dmoenc_wmvdmoe2v2'):
                self.wmvEncoder = 'dmoenc_wmvdmoe2v2'
                self.wmvVersion = gstreamer.get_plugin_version('pitfdll')
            else:
                version  = gstreamer.get_plugin_version('pitfdll')
                if not version:
                    self.warning('could not find pitfdll.')
                    m = Error(T_( 
                        N_("This host is missing the 'gst-pitfdll' GStreamer plug-in.\n")))
                else:
                    self.warning('could not find dmoenc_wmvdmoe2v2, probably missing DLL, or old registry.')
                    m = Error(T_(
                        N_("This host is missing the Windows encoder DLL.\n")))
                self.wmvEncoder = None
                self.addMessage(m)
        else:
            # First look for Fluendo WMV encoder.
            if gstreamer.element_factory_exists('fluwmvenc'):
                self.debug('found fluwmvenc, using it.')
                self.wmvEncoder = 'fluwmvenc'
                self.wmvVersion = gstreamer.get_plugin_version('fluwmvenc')
            elif gstreamer.element_factory_exists('dmoenc_wmvdmoe2v3'):
                self.debug('could not find fluwmvenc, found dmoenc_wmvdmoe2v3.')
                self.wmvEncoder = 'dmoenc_wmvdmoe2v3'
                self.wmvVersion = gstreamer.get_plugin_version('pitfdll')
            else:
                self.warning('could not find any WMV encoder.')
                m = Error(T_(
                    N_("This host is missing the WMV encoder plug-ins.\n")))
                self.wmvEncoder = None
                self.addMessage(m)

        self.uiState.set('encoder', self.wmvEncoder)
        self.uiState.set('version', self.wmvVersion)

        return defer.succeed(None)

    def get_pipeline_string(self, properties):
        return "ffmpegcolorspace ! %s name=encoder" % self.wmvEncoder

    def configure_pipeline(self, pipeline, properties):
        element = pipeline.get_by_name('encoder')
        if properties.has_key('bitrate'):
            element.set_property('bitrate', properties['bitrate'])
        if properties.has_key('complexity'):
            element.set_property('complexity', properties['complexity'])
        if properties.has_key('sharpness'):
            element.set_property('sharpness', properties['sharpness'])
        if properties.has_key('key-frame-distance'):
            element.set_property('key-frame-distance', properties['key-frame-distance'])
        if properties.has_key('buffer-delay'):
            element.set_property('buffer-delay', properties['buffer-delay'])

        self.uiState.set('complexity', element.get_property('complexity'))
        self.uiState.set('bitrate', formatStorage(element.get_property('bitrate')) + 'bit/s')

    def modify_property_Bitrate(self, value):
        if not self.checkPropertyType('bitrate', value, int):
            return False
        self.modify_element_property('encoder', 'bitrate', value)
        return True
