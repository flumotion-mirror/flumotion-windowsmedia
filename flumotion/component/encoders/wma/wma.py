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

from flumotion.component import feedcomponent


class WMAEncoder(feedcomponent.ParseLaunchComponent):
    checkTimestamp = True
    checkOffset = True
    resampler = 'legacyresample'

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
        if 'samplerate' in properties:
            gstElements.insert(1, self.resampler)
            gstElements.insert(2, 'audio/x-raw-int,rate=%d'
                    % properties['samplerate'])
        if 'drop-probability' in properties:
            gstElements.insert(0, 'identity drop-probability=%f silent=TRUE'
                    % properties['drop-probability'])
        return " ! ".join(gstElements)

    def configure_pipeline(self, pipeline, properties):
        element = pipeline.get_by_name('encoder')
        if 'bitrate' in properties:
            element.set_property('bitrate', properties['bitrate'])
