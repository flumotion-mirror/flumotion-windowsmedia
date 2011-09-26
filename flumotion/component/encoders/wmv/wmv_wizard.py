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

import gettext
import os

from zope.interface import implements

from flumotion.admin.gtk.basesteps import VideoEncoderStep
from flumotion.admin.assistant.interfaces import IEncoderPlugin
from flumotion.admin.assistant.models import VideoEncoder

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
