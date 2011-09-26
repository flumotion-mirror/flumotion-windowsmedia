AUTOMAKE_OPTIONS = dist-bzip2 no-dist-gzip 1.8

ACLOCAL_AMFLAGS = -I common

include $(srcdir)/common/changelog.mk
include $(srcdir)/common/common.mk
include $(srcdir)/common/pychecker.mk
include $(srcdir)/common/deb.mk

SUBDIRS = flumotion conf common misc

EXTRA_DIST = \
	env.in \
        .version \
        RELEASE \
	LICENSE.LGPL \
	flumotion-windowsmedia.spec

PYCHECKER_WHITELIST = flumotion/component/*/*/*.py \
                      flumotion/component/*/*.py \
PYCHECKER_BLACKLIST =

COVERAGE_MODULES = \
	flumotion.component.muxers.asf \
	flumotion.component.encoders.wmv \
	flumotion.component.encoders.wma

BUILT_SOURCES = $(top_srcdir)/.version
$(top_srcdir)/.version:                                                                                 
	echo $(VERSION) > $@-t && mv $@-t $@

dist: changelog
