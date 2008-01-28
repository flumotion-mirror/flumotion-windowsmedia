include $(top_srcdir)/common/python.mk

component_PYTHON = __init__.py wma.py wma_wizard.py
componentdir = $(libdir)/flumotion/python/flumotion/component/encoders/wma
component_DATA = \
	wma.xml

clean-local:
	rm -rf *.pyc *.pyo

EXTRA_DIST = $(component_DATA)
