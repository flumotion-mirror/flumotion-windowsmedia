AUTOMAKE_OPTIONS = dist-bzip2 no-dist-gzip 1.8

ACLOCAL_AMFLAGS = -I common

include $(srcdir)/common/common.mk
include $(srcdir)/common/pychecker.mk

SUBDIRS = flumotion common

EXTRA_DIST = \
	LICENSE.Flumotion \
	flumotion-windowsmedia.spec \
	misc/preamble.py

CLEANFILES = misc/setup.py

PYCHECKER_WHITELIST =
PYCHECKER_BLACKLIST =

COVERAGE_MODULES = \
	flumotion.component.wmv \
	flumotion.component.wma
