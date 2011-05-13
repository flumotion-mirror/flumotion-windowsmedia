# -*- Mode: Python; test-case-name: flumotion.test.test_http -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006,2007,2008 Fluendo, S.L. (www.fluendo.com).
# All rights reserved.

# This file may be distributed and/or modified under the terms of
# the GNU General Public License version 2 as published by
# the Free Software Foundation.
# This file is distributed without any warranty; without even the implied
# warranty of merchantability or fitness for a particular purpose.
# See "LICENSE.GPL" in the source distribution for more information.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

import gst

from twisted.internet import reactor, error, defer
from zope.interface import implements

from flumotion.common import errors
from flumotion.common import messages, interfaces
from flumotion.common.i18n import N_, gettexter
from flumotion.common.planet import moods
from flumotion.component import feedcomponent
from flumotion.component.component import moods

from flumotion.component.common.wms import common, asfparser
from flumotion.component.consumers.wms import pull_producer

__all__ = ['WMSMedium', 'WMSStreamer']
__version__ = "$Rev: 8472 $"
T_ = gettexter()


class WMSMedium(feedcomponent.FeedComponentMedium):

    def __init__(self, comp):
        """
        @type comp: L{Stats}
        """
        feedcomponent.FeedComponentMedium.__init__(self, comp)


class WMSConsumer(feedcomponent.ParseLaunchComponent):
    implements(interfaces.IStreamingComponent)

    logCategory = 'wms-consumer'

    componentMediumClass = WMSMedium

    keepStreamheaderForLater = True
    dropStreamHeaders = False

    def init(self):
        reactor.debug = True
        self.debug("WMS streamer initialising")

        self.caps = None
        self.mountPoint = None

        self.port = None
        self.iface = None

        self._parser = None
        self._factory = None
        self._tport = None

    def get_pipeline_string(self, properties):
        return "appsink name=appsink sync=false"

    def check_properties(self, props, addMessage):
        pass

    def configure_pipeline(self, pipeline, properties):
        appsink = pipeline.get_by_name('appsink')
        
        # Reset the parser if we got a new buffer with IN_CAPS
        appsink.get_pad('sink').add_buffer_probe(self._check_incaps)

        appsink.connect("new-preroll", self._new_preroll)
        appsink.connect("new-buffer", self._new_buffer)
        appsink.connect("eos", self._eos)
        appsink.set_property('emit-signals', True)

        self.port = int(properties.get('port', 8800))
 
    def _check_incaps(self, pad, buffer):
        if buffer.flag_is_set(gst.BUFFER_FLAG_IN_CAPS):
            self.debug("Restarting the parser to process the new stream.")
            self._parser.reset()
        return True

    def __repr__(self):
        return '<WMSStreamer (%s)>' % self.name

    def do_stop(self):
        if self._factory:
            self._factory.stop()
        if self._tport:
            self._tport.stopListening()


    def do_setup(self):
        try:
            self.debug('Listening on %d' % self.port)
            self._parser = asfparser.ASFParser(self)
            self._factory = pull_producer.WMSPullFactory()
            self._tport = reactor.listenTCP(self.port, self._factory)
        except error.CannotListenError:
            t = 'Port %d is not available.' % self.port
            self.warning(t)
            m = messages.Error(T_(N_(
                "Network error: TCP port %d is not available."),
                                  self.port))
            self.addMessage(m)
            self.setMood(moods.sad)
            return defer.fail(errors.ComponentSetupHandledError(t))

    ### START OF THREAD-AWARE CODE (called from non-reactor threads)

    def _new_preroll(self, appsink):
        buffer = appsink.emit('pull-preroll')

    def _new_buffer(self, appsink):
        buffer = appsink.emit('pull-buffer')
        reactor.callFromThread(self._processBuffer, buffer)

    def _eos(self, appsink):
        pass

    ### END OF THREAD-AWARE CODE

    def _processBuffer(self, buffer):
        if self.getMood() == moods.sad.value: return
        self._parser.pushData(buffer.data)

    ### ASF Parser Callbacks ###

    def pushHeaders(self, parser, header_obj, data_obj):
        self._factory.pushHeaders(header_obj, data_obj)

    def pushPacket(self, parser, packet):
        self._factory.pushPacket(packet)


