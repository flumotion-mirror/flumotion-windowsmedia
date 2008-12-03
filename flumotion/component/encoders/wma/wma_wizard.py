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

from zope.interface import implements

from flumotion.admin.gtk.basesteps import AudioEncoderStep
from flumotion.admin.assistant.interfaces import IEncoderPlugin
from flumotion.admin.assistant.models import AudioEncoder

_ = gettext.gettext
__version__ = "$Rev$"


class WMAAudioEncoder(AudioEncoder):
    componentType = 'wma-encoder'

    def __init__(self):
        super(WMAAudioEncoder, self).__init__()

        self.properties.bitrate = 64

    def getProperties(self):
        properties = super(WMAAudioEncoder, self).getProperties()
        properties['bitrate'] *= 1000
        return properties


class WMAStep(AudioEncoderStep):
    name = 'WindowsMediaAudioEncoder'
    title = _('Windows Media Audio Encoder')
    sidebarName = _('Windows Media Audio')
    componentType = 'wma'

    # WizardStep

    def setup(self):
        self.bitrate.set_range(5, 128)
        self.bitrate.set_value(64)

        self.bitrate.data_type = int

        self.add_proxy(self.model.properties, ['bitrate'])

    def workerChanged(self, worker):
        self.model.worker = worker
        self.wizard.requireElements(worker, 'fluwmaenc', 'fluasfmux')


class WMAWizardPlugin(object):
    implements(IEncoderPlugin)
    def __init__(self, wizard):
        self.wizard = wizard
        self.model = WMAAudioEncoder()

    def getConversionStep(self):
        return WMAStep(self.wizard, self.model)
