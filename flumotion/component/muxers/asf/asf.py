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

class ASFMuxer(feedcomponent.ParseLaunchComponent):
    def get_pipeline_string(self, properties):
        pipeline = 'fluasfmux broadcast=true name=muxer '

        for eater in self.config['source']:
            if gst.gst_version < (0, 9):
                pipeline += '{ @ eater:%s @ ! queue max-size-buffers=16 } ! muxer. '\
                    % eater
            else:
                pipeline += '@ eater:%s @ ! queue max-size-buffers=16 ! muxer. '\
                    % eater

        pipeline += 'muxer.'

        return pipeline
