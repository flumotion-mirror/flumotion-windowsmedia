include $(top_srcdir)/common/python.mk

componentdir = $(libdir)/flumotion/python/flumotion/component/consumers/wms

component_PYTHON = \
	__init__.py \
	pull_producer.py \
	wmsconsumer.py \
	wmsconsumer.xml
	

component_DATA = 

clean-local:
	rm -rf *.pyc *.pyo

EXTRA_DIST = $(component_DATA)
