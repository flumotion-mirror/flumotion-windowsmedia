include $(top_srcdir)/common/python.mk

component_PYTHON = __init__.py wms.py asfparse.py queue.py
componentdir = $(libdir)/flumotion/python/flumotion/component/producers
component_DATA = \
	wms.xml

clean-local:
	rm -rf *.pyc *.pyo

EXTRA_DIST = $(component_DATA)
