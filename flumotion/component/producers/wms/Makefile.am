include $(top_srcdir)/common/python.mk

componentdir = $(libdir)/flumotion/python/flumotion/component/producers/wms
component_PYTHON = __init__.py wms.py asfparse.py queue.py digest.py
component_DATA = \
	wms.xml

clean-local:
	rm -rf *.pyc *.pyo

EXTRA_DIST = $(component_DATA)
