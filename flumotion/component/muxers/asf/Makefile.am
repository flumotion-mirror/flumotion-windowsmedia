include $(top_srcdir)/common/python.mk

component_PYTHON = __init__.py asf.py wizard_gtk.py
componentdir = $(libdir)/flumotion/python/flumotion/component/muxers/asf
component_DATA = \
	asf.xml

clean-local:
	rm -rf *.pyc *.pyo

EXTRA_DIST = $(component_DATA)
