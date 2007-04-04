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
        return "identity drop-probablity=0.0001 silent=TRUE ! audioconvert ! fluwmaenc name=encoder"

    def configure_pipeline(self, pipeline, properties):
        element = pipeline.get_by_name('encoder')
        if properties.has_key('bitrate'):
            element.set_property('bitrate', properties['bitrate'])
