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

import os

from flumotion.component import feedcomponent

class WMVEncoder(feedcomponent.ParseLaunchComponent):
    def get_pipeline_string(self, properties):
        return "ffmpegcolorspace ! dmoenc_wmvdmoe2v3 name=encoder"

    def configure_pipeline(self, pipeline, properties):
        element = component.pipeline.get_by_name('encoder')
        if properties.has_key('bitrate'):
            element.set_property('bitrate', properties['bitrate'])
