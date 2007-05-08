# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2007 Fluendo, S.L. (www.fluendo.com).
# All rights reserved.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

import threading

class InterruptedException(Exception):
    """
    The operation was interrupted
    """

class AsyncQueue(object):
    """
    A very simple interruptable async queue of unlimited size
    """

    def __init__(self):
        self._queue = []
        self._cond = threading.Condition()

    def push(self, object):
        """
        Push an object onto the async queue
        """
        self._cond.acquire()
        self._queue.append(object)
        self._cond.notify()
        self._cond.release()

    def pop(self):
        """
        Pop an object off the async queue and return it.
        This blocks if the queue is empty.
        Raises InterruptedException if, while blocked, another thread calls
        unblock() 
        """
        self._cond.acquire()
        if len(self._queue) == 0:
            self._cond.wait()

        if len(self._queue) == 0:
            self._cond.release()
            raise InterruptedException()

        obj = self._queue.pop(0)
        self._cond.release()

        return obj

    def unblock(self):
        """
        Unblock any blocked calls to pop(). Those calls will then
        raise an InterruptedException instead of returning an object.
        """
        self._cond.acquire()
        self._cond.notifyAll()
        self._cond.release()

    def clear(self):
        """
        Clear the queue, and unblock any blocked calls to pop()
        """
        self._cond.acquire()
        self._cond.notifyAll()
        self._queue = []
        self._cond.release()

