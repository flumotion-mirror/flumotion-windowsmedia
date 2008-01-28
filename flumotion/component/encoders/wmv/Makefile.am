include $(top_srcdir)/common/python.mk

component_PYTHON = __init__.py wmv.py admin_gtk.py wmv_wizard.py
componentdir = $(libdir)/flumotion/python/flumotion/component/encoders/wmv
component_DATA = \
	wmv.glade \
	wmv.xml \
	wmv-wizard.glade

clean-local:
	rm -rf *.pyc *.pyo

EXTRA_DIST = $(component_DATA)
