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

from flumotion.common import gstreamer
from flumotion.component import feedcomponent


class WMAEncoder(feedcomponent.EncoderComponent):
    checkTimestamp = True
    checkOffset = True

    def do_check(self):
        self.debug('running fluwmaenc check')
        from flumotion.worker.checks import check
        d = check.checkPlugin('fluwmaenc', 'gst-fluendo-wmaenc')

        def cb(result):
            for m in result.messages:
                self.addMessage(m)
        d.addCallback(cb)
        return d

    def get_pipeline_string(self, properties):
        gstElements = ['audioconvert', 'fluwmaenc name=encoder']

        channels = properties.get('channels', 2)

        if 'samplerate' in properties:
            resampler = 'audioresample'
            if gstreamer.element_factory_exists('legacyresample'):
                resampler = 'legacyresample'

            gstElements.insert(1, resampler)
            gstElements.insert(2, 'audio/x-raw-int,rate=%d,channels=%d'
                    % (properties['samplerate'], channels))

        if 'drop-probability' in properties:
            gstElements.insert(0, 'identity drop-probability=%f silent=TRUE'
                    % properties['drop-probability'])

        return " ! ".join(gstElements)

    def configure_pipeline(self, pipeline, properties):
        element = pipeline.get_by_name('encoder')
        if 'bitrate' in properties:
            element.set_property('bitrate', properties['bitrate'])
