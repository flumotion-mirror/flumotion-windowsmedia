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

from flumotion.component.base.admin_gtk import BaseAdminGtk
from flumotion.component.base.baseadminnode import BaseAdminGtkNode


class WMVEncoderAdminGtkNode(BaseAdminGtkNode):
    logCategory = 'wmv'
    gladeFile = 'flumotion/component/encoders/wmv/wmv.glade'
    uiStateHandlers = None

    def haveWidgetTree(self):
        self.widget = self.wtree.get_widget('widget-wmv')
        self._encoder = self.wtree.get_widget('value-encoder')
        self._version = self.wtree.get_widget('value-version')
        self._bitrate = self.wtree.get_widget('value-bitrate')
        self._complexity = self.wtree.get_widget('value-complexity')

    def setUIState(self, state):
        BaseAdminGtkNode.setUIState(self, state)
        if not self.uiStateHandlers:
            self.uiStateHandlers = {'encoder': self.encoderSet,
                                    'version': self.versionSet,
                                    'bitrate': self.bitrateSet,
                                    'complexity': self.complexitySet }
        for k, handler in self.uiStateHandlers.items():
            handler(state.get(k))

    def stateSet(self, state, key, value):
        handler = self.uiStateHandlers.get(key, None)
        if handler:
            handler(value)

    def encoderSet(self, encoder):
    	if self._encoder:
            self._encoder.set_text(str(encoder))

    def versionSet(self, version):
        if self._version:
            self._version.set_text(str(version))

    def bitrateSet(self, bitrate):
    	if self._bitrate:
            self._bitrate.set_text(str(bitrate))

    def complexitySet(self, complexity):
        if self._complexity:
            self._complexity.set_text(str(complexity))

class WMVEncoderAdminGtk(BaseAdminGtk):
    def setup(self):
        wmvnode = WMVEncoderAdminGtkNode(self.state, self.admin,
                                         title="Encoder")
        self.nodes['Encoder'] = wmvnode

        return BaseAdminGtk.setup(self)
