include $(top_srcdir)/common/python.mk

componentdir = $(libdir)/flumotion/python/flumotion/component/common

SUBDIRS = \
        wms
component_PYTHON = \
	__init__.py \
	compcommon.xml \
	http.py

component_DATA = 

clean-local:
	rm -rf *.pyc *.pyo

EXTRA_DIST = $(component_DATA)
