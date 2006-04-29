AUTOMAKE_OPTIONS = dist-bzip2 no-dist-gzip 1.8

ACLOCAL_AMFLAGS = -I common

include $(srcdir)/common/common.mk
include $(srcdir)/common/pychecker.mk

SUBDIRS = flumotion common misc

EXTRA_DIST = \
	LICENSE.Flumotion \
	flumotion-windowsmedia.spec

PYCHECKER_WHITELIST = flumotion/component/*/*.py
PYCHECKER_BLACKLIST =

COVERAGE_MODULES = \
	flumotion.component.asf \
	flumotion.component.wmv \
	flumotion.component.wma
