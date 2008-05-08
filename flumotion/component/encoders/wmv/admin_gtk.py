# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006,2007 Fluendo, S.A. (www.fluendo.com).
# All rights reserved.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

from gettext import gettext as _

from flumotion.component.base import admin_gtk

class WMVEncoderAdminGtkNode(admin_gtk.BaseAdminGtkNode):
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
        admin_gtk.BaseAdminGtkNode.setUIState(self, state)
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

class WMVEncoderAdminGtk(admin_gtk.BaseAdminGtk):
    def setup(self):
        wmvnode = WMVEncoderAdminGtkNode(self.state, self.admin,
                                         title="Encoder")
        self.nodes['Encoder'] = wmvnode

        return admin_gtk.BaseAdminGtk.setup(self)
