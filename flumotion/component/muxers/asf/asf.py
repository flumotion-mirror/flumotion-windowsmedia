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

import gst

from flumotion.component import feedcomponent

class ASFMuxer(feedcomponent.MuxerComponent):
    checkOffset = True

    def do_check(self):
        self.debug('running fluasfmux check')
        from flumotion.worker.checks import check
        d = check.checkPlugin('fluasfmux', 'gst-fluendo-asfmux')
        def cb(result):
            for m in result.messages:
                self.addMessage(m)
        d.addCallback(cb)
        return d

    def get_muxer_string(self, properties):
        return 'fluasfmux broadcast=true name=muxer '

    def configure_pipeline(self, pipeline, properties):
        element = pipeline.get_by_name('muxer')
        if properties.has_key('preroll-time'):
            element.set_property('preroll-time', properties['preroll-time'])
        super(ASFMuxer, self).configure_pipeline(pipeline, properties)
