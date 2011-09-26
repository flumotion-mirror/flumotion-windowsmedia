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
        self.samplerate = 44100

    def getProperties(self):
        properties = super(WMAAudioEncoder, self).getProperties()
        properties.bitrate  *= 1000
        properties.samplerate = self.samplerate
        return properties


class WMAStep(AudioEncoderStep):
    name = 'WindowsMediaAudioEncoder'
    title = _('Windows Media Audio Encoder')
    sidebarName = _('Windows Media Audio')
    componentType = 'wma'

    # WizardStep

    def setup(self):
        producer = self.wizard.getScenario().getAudioProducer(self.wizard)
        samplerate = int(producer.getSamplerate())
        self.model.samplerate = samplerate
        self.debug('Samplerate of audio producer: %r' % samplerate)

        if samplerate == 8000:
            self.bitrate.set_range(12, 12)
            self.model.properties.bitrate = 12
        elif samplerate == 16000:
            self.bitrate.set_range(16, 20)
            self.model.properties.bitrate = 20
        elif samplerate == 22050:
            self.bitrate.set_range(20, 32)
            self.model.properties.bitrate = 32
        elif samplerate == 32000:
            self.bitrate.set_range(32, 48)
            self.model.properties.bitrate = 48
        elif samplerate == 44100 or samplerate == 48000:
            self.bitrate.set_range(64, 198)
            self.model.properties.bitrate = 128

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
