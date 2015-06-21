#!/usr/bin/env python
#
# ovirt-node-hosted-engine-setup.py - Copyright (C) 2015 Red Hat, Inc.
# Written by Ryan Barry <rbarry@redhat.com>
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

import sys
from ovirt.node.utils import process


def getch():
    """
    Read one character, then return. There's no implementation of this in
    stdlib, and it's nicer than waiting for real input (which requires enter),
    since "Press any key to continue..." actually works
    """
    import tty
    import termios
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def run(args):
    # Instead of checking for an exception, return the actual return code
    # from ovirt-hosted-engine-setup in case we decide to check for some
    # real value on it in the future

    rc = process.call(["ovirt-hosted-engine-setup"] + args)
    if rc != 0:
        print("Something went wrong setting up hosted engine, or the "
              "setup process was cancelled.\n\nPress any key to continue...")
        getch()
    sys.exit(rc)

if __name__ == "__main__":
    # Just a wrapper. Strip off the name of this script and pass everything
    # else to ovirt-hosted-engine-setup

    run(sys.argv[1:])
