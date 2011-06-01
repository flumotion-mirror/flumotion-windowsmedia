include $(top_srcdir)/common/python.mk

componentdir = $(libdir)/flumotion/python/flumotion/component/common

SUBDIRS = \
        wms
component_PYTHON = \
	compcommon.xml \
	http.py

component_DATA = 

clean-local:
	rm -rf *.pyc *.pyo

# We do not install __init__.py since it would override flumotion's
EXTRA_DIST = $(component_DATA) __init__.py
