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

from zope.interface import implements

from flumotion.wizard.basesteps import AudioEncoderStep
from flumotion.wizard.interfaces import IEncoderPlugin
from flumotion.wizard.models import AudioEncoder

__version__ = "$Rev$"


class WMAAudioEncoder(AudioEncoder):
    component_type = 'wma-encoder'

    def __init__(self):
        super(WMAAudioEncoder, self).__init__()

        self.properties.bitrate = 64

    def getProperties(self):
        properties = super(WMAAudioEncoder, self).getProperties()
        properties['bitrate'] *= 1000
        return properties


class WMAStep(AudioEncoderStep):
    name = 'Windows Media Audio encoder'
    sidebarName = 'Windows Media Audio'
    component_type = 'wma'

    # WizardStep

    def setup(self):
        self.bitrate.set_range(5, 128)
        self.bitrate.set_value(64)

        self.bitrate.data_type = int

        self.add_proxy(self.model.properties, ['bitrate'])

    def workerChanged(self, worker):
        self.model.worker = worker
        self.wizard.requireElements(worker, 'fluwmaenc')


class WMAWizardPlugin(object):
    implements(IEncoderPlugin)
    def __init__(self, wizard):
        self.wizard = wizard
        self.model = WMAAudioEncoder()

    def getConversionStep(self):
        return WMAStep(self.wizard, self.model)
