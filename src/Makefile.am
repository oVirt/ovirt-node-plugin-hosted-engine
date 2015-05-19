# Copyright (C) 2014 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA  02110-1301, USA.  A copy of the GNU General Public License is
# also available at http://www.gnu.org/copyleft/gpl.html.

nodeconfdir = $(sysconfdir)/sysconfig

pyovirtsetupdir = $(pythondir)/ovirt/node/setup/hostedengine
pyovirtsetup_PYTHON = \
  hosted_engine_page.py \
  hosted_engine_model.py \
  __init__.py \
  config.py

# Requires python-pep8 package (Fedora)
check-local: check-local-pep8

check-local-pep8:
	find $(srcdir) -name \*.py | sort | uniq | xargs pep8 --repeat --statistics --count

check-local-doctest:
	find $(srcdir) -name \*.py | sort | uniq | xargs nose -q --with-doctest

clean-local:
	rm -f config.py
