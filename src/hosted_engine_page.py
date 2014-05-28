#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# hosted_engine_page.py - Copyright (C) 2014 Red Hat, Inc.
# Written by Joey Boggs <jboggs@redhat.com>
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

from ovirt.node import plugins, ui, utils, valid
from ovirt.node.plugins import Changeset
from ovirt.node.config.defaults import NodeConfigFileSection
from ovirt_hosted_engine_ha.client import client
import os
import tempfile
import subprocess #swap with process.call

VM_CONF_PATH = "/etc/ovirt-hosted-engine/vm.conf"
HOSTED_ENGINE_SETUP_DIR = "/data/ovirt-hosted-engine-setup"
"""
Configure Hosted Engine
"""

def image_retrieve(url, dest):
    dest = os.path.join(dest, os.path.basename(url))
    try:
        cmd = ["wget", "-nd", "--no-check-certificate", "--timeout=30",
               "--tries=3", "-O", dest, url]
        subprocess.check_call(cmd)
    except:
        raise RuntimeError("Error Downloading ISO/OVA Image")

def get_hosted_cfg(imagepath, pxe):
    ova = False
    boot = None
    txt = "[environment:default]\n"
    if imagepath.endswith(".iso"):
        boot = "cdrom"
        txt += "OVEHOSTED_VM/vmCDRom=str:%s\n" % \
                    os.path.join(HOSTED_ENGINE_SETUP_DIR, imagepath)
    elif imagepath.endswith(".gz"):
        boot = "disk"
        ova = True
        ova_path = os.path.join(HOSTED_ENGINE_SETUP_DIR,
                                os.path.basename(imagepath))
    elif pxe:
        boot = "pxe"
    txt += "OVEHOSTED_VM/vmBoot=str:%s\n" % boot
    if ova:
        txt += "OVEHOSTED_VM/ovfArchive=str:%s\n" % ova_path
    else:
        txt += "OVEHOSTED_VM/ovfArchive=none:None\n"
    return txt

class Plugin(plugins.NodePlugin):
    _server = None

    def name(self):
        return "Hosted Engine"

    def rank(self):
        return 110

    def update(self, imagepath):
        (valid.Empty() | valid.Text())(imagepath)
        return {"OVIRT_HOSTED_ENGINE_IMAGE_PATH": imagepath}

    def model(self):
        cfg = HostedEngine().retrieve()

        conf_status = os.path.exists(VM_CONF_PATH)
        vm = None
        if conf_status:
            with open(VM_CONF_PATH) as f:
                for line in f:
                    if "vmName" in line:
                        vm = line.strip().split("=")[1]

        ha_cli = client.HAClient()
        try:
            vm_status = ha_cli.get_all_host_stats()
        except:
            vm_status = "Cannot connect to HA daemon, please check the logs"

        model = {
            "hosted_engine.enabled": str(conf_status),
            "hosted_engine.vm": vm,
            "hosted_engine.status": str(vm_status),
            "hosted_engine.diskpath": cfg["imagepath"] or "",
            "hosted_engine.pxe": cfg["pxe"] or False}

        return model

    def validators(self):
        return {}

    def ui_content(self):
        ws = [ui.Header("header[0]", "Hosted Engine Setup"),
              ui.KeywordLabel("hosted_engine.enabled",
                              ("Hosted Engine: ")),
              ui.KeywordLabel("hosted_engine.vm",
                              ("Engine VM: ")),
              ui.Divider("divider[0]"),
              ui.KeywordLabel("hosted_engine.status",
                              ("Engine Status: ")),

              ui.Divider("divider[0]"),
              ui.Entry("hosted_engine.diskpath", "Engine ISO/OVA Path:"),
              ui.Divider("divider[1]"),
              ui.Checkbox("hosted_engine.pxe", "PXE Boot Engine VM")
              ]

        page = ui.Page("page", ws)
        page.buttons = [ui.Button("action.setupengine",
                                  "Setup Hosted Engine")
                        ]
        self.widgets.add(page)
        return page

    def on_change(self, changes):
        pass

    def on_merge(self, effective_changes):
        self.logger.info("Saving Hosted Engine Config")
        changes = Changeset(self.pending_changes(False))
        effective_model = Changeset(self.model())
        effective_model.update(effective_changes)

        self.logger.debug("Changes: %s" % changes)
        self.logger.debug("Effective Model: %s" % effective_model)

        engine_keys = ["hosted_engine.diskpath", "hosted_engine.pxe"]

        if effective_changes.contains_any(["action.setupengine"]):
            HostedEngine().update(*effective_model.values_for(engine_keys))

            imagepath = effective_model["hosted_engine.diskpath"]
            pxe = effective_model["hosted_engine.pxe"]
            if not os.path.exists(HOSTED_ENGINE_SETUP_DIR):
                os.makedirs(HOSTED_ENGINE_SETUP_DIR)
            localpath = os.path.join(HOSTED_ENGINE_SETUP_DIR, os.path.basename(imagepath))
            if not os.path.exists(localpath):
                image_retrieve(imagepath, HOSTED_ENGINE_SETUP_DIR)
            temp_fd, temp_cfg_file = tempfile.mkstemp()
            os.close(temp_fd)
            hosted_cfg = get_hosted_cfg(os.path.basename(imagepath), pxe)
            with open(temp_cfg_file, "w") as f:
                 f.write(hosted_cfg)
            self.logger.info(temp_cfg_file)
            self.logger.info(hosted_cfg)
            self.logger.debug("Starting drop to setup")
            utils.console.writeln("Beginning Hosted Engine Setup ...")

            def open_console():
                utils.process.call("reset; screen ovirt-hosted-engine-setup" + \
                                   " --config-append=%s" % temp_cfg_file, \
                                   shell=True)

            def return_ok(dialog, changes):
                with self.application.ui.suspended():
                    open_console()

            try:
                txt = "Setup will be ran with screen enabled that can be "
                txt += "reconnected in the event of a timeout or connection "
                txt += "failure.\n"
                txt += "\nIt can be reconnected by running 'screen -d -r'"

                dialog = ui.ConfirmationDialog("dialog.shell",
                                               "Begin Hosted Engine Setup", txt
                                               )

                dialog.buttons[0].on_activate.clear()
                dialog.buttons[0].on_activate.connect(ui.CloseAction())
                dialog.buttons[0].on_activate.connect(return_ok)
                self.application.show(dialog)

            except:
                # Error when the UI is not running
                open_console()
        return self.ui_content()

class HostedEngine(NodeConfigFileSection):
    keys = ("OVIRT_HOSTED_ENGINE_IMAGE_PATH",
            "OVIRT_HOSTED_ENGINE_PXE",
            )

    @NodeConfigFileSection.map_and_update_defaults_decorator
    def update(self, imagepath, pxe):
        (valid.Empty() | valid.Text())(imagepath)
        (valid.Boolean()(pxe))
        return {"OVIRT_HOSTED_ENGINE_IMAGE_PATH": imagepath,
                "OVIRT_HOSTED_ENGINE_PXE": "yes" if pxe else None}

