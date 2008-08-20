# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2008 Fluendo, S.L. (www.fluendo.com).
# All rights reserved.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

import gettext
import os

from zope.interface import implements

from flumotion.admin.gtk.basesteps import VideoEncoderStep
from flumotion.admin.gtk.interfaces import IEncoderPlugin
from flumotion.admin.gtk.models import VideoEncoder

__version__ = "$Rev$"
_ = gettext.gettext


class WMVVideoEncoder(VideoEncoder):
    componentType = 'wmv-encoder'
    def __init__(self):
        super(WMVVideoEncoder, self).__init__()
        self.has_quality = True
        self.has_bitrate = False

        self.properties.bitrate = 256

    def getProperties(self):
        properties = super(WMVVideoEncoder, self).getProperties()
        properties['bitrate'] *= 1000
        return properties


class WMVStep(VideoEncoderStep):
    name = 'Windows Media Video Encoder'
    title = _('Windows Media Video Encoder')
    sidebarName = _('Windows Media Video')
    gladeFile = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'wmv-wizard.glade')
    componentType = 'wmv'

    # WizardStep

    def setup(self):
        self.bitrate.data_type = int

        self.add_proxy(self.model.properties, ['bitrate'])

    def workerChanged(self, worker):
        self.model.worker = worker
        self.wizard.requireElements(worker, 'fluwmvenc')


class WMVWizardPlugin(object):
    implements(IEncoderPlugin)
    def __init__(self, wizard):
        self.wizard = wizard
        self.model = WMVVideoEncoder()

    def getConversionStep(self):
        return WMVStep(self.wizard, self.model)
