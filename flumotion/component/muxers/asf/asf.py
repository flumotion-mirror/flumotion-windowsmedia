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

import gst

from flumotion.component import feedcomponent

class ASFMuxer(feedcomponent.MultiInputParseLaunchComponent):
    def get_muxer_string(self, properties):
        return 'fluasfmux broadcast=true name=muxer '
    
    def configure_pipeline(self, pipeline, properties):
        element = pipeline.get_by_name('muxer')
        if properties.has_key('preroll-time'):
            element.set_property('preroll-time', properties['preroll-time'])    
