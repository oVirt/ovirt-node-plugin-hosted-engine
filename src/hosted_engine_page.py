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

from urlparse import urlparse

from ovirt.node import plugins, ui, utils, valid, log
from ovirt.node.plugins import Changeset
from ovirt.node.config.defaults import NodeConfigFileSection
from ovirt_hosted_engine_ha.client import client

import os
import requests
import tempfile
import threading
import time

VM_CONF_PATH = "/etc/ovirt-hosted-engine/vm.conf"
HOSTED_ENGINE_SETUP_DIR = "/data/ovirt-hosted-engine-setup"
"""
Configure Hosted Engine
"""


LOGGER = log.getLogger(__name__)


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
    if boot:
        txt += "OVEHOSTED_VM/vmBoot=str:%s\n" % boot
    else:
        txt += "OVEHOSTED_VM/vmBoot=none:None\n"
    if ova:
        txt += "OVEHOSTED_VM/ovfArchive=str:%s\n" % ova_path
    else:
        txt += "OVEHOSTED_VM/ovfArchive=none:None\n"
    return txt


class Plugin(plugins.NodePlugin):
    _server = None
    _show_progressbar = False
    _model = {}

    def __init__(self, application):
        super(Plugin, self).__init__(application)

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
            "hosted_engine.display_message": "",
            "hosted_engine.pxe": cfg["pxe"] or False}

        self._model.update(model)

        return self._model

    def validators(self):
        return {"hosted_engine.diskpath": valid.Empty() | valid.URL()}

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
              ui.Entry("hosted_engine.diskpath",
                       "Engine ISO/OVA URL for download:"),
              ui.Divider("divider[1]"),
              ui.Checkbox("hosted_engine.pxe", "PXE Boot Engine VM")
              ]

        if self._show_progressbar:
            if "progress" in self._model:
                ws.append(ui.ProgressBar("download.progress",
                                         int(self._model["progress"])))
            else:
                ws.append(ui.ProgressBar("download.progress", 0))

            ws.append(ui.KeywordLabel("download.status", ""))

        page = ui.Page("page", ws)
        page.buttons = [ui.Button("action.setupengine",
                                  "Setup Hosted Engine")
                        ]
        self.widgets.add(page)
        return page

    def on_change(self, changes):
        pass

    def on_merge(self, effective_changes):
        self._configure_ISO_OVA = False
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
            localpath = os.path.join(HOSTED_ENGINE_SETUP_DIR,
                                     os.path.basename(imagepath))
            temp_fd, self.temp_cfg_file = tempfile.mkstemp()
            os.close(temp_fd)

            if not imagepath and not pxe:
                self._model['display_message'] = "\n\nYou must enter a URL or " \
                    " choose PXE to install the Engine VM"
                self.show_dialog()
                return self.ui_content()

            path_parsed = urlparse(imagepath)
            if not path_parsed.scheme:
                self._model['display_message'] = "\nCouldn't parse URL. " +\
                                                 "please check it manually."
                self.show_dialog()
            elif os.path.exists(localpath):
                hosted_cfg = get_hosted_cfg(os.path.basename(imagepath), pxe)
                with open(self.temp_cfg_file, "w") as f:
                    f.write(hosted_cfg)
                self.logger.info(self.temp_cfg_file)
                self.logger.info(hosted_cfg)
                self.logger.debug("Starting drop to setup")
                utils.console.writeln("Beginning Hosted Engine Setup ...")
                self._configure_ISO_OVA = True
                self.show_dialog()
            elif path_parsed.scheme == 'http' or \
                    path_parsed.scheme == 'https':
                self._show_progressbar = True
                self.application.show(self.ui_content())
                self._image_retrieve(imagepath, HOSTED_ENGINE_SETUP_DIR)

        return self.ui_content()

    def show_dialog(self):
        def open_console():
            utils.process.call("reset; screen ovirt-hosted-engine-setup" +
                               " --config-append=%s" % self.temp_cfg_file,
                               shell=True)

        def return_ok(dialog, changes):
            with self.application.ui.suspended():
                open_console()
        if self.application.current_plugin() is self:
            try:
                if self._configure_ISO_OVA:
                    txt = "Setup will be ran with screen enabled that can be "
                    txt += "reconnected in the event of a timeout or "
                    txt += "connection failure.\n"
                    txt += "\nIt can be reconnected by running 'screen -d -r'"

                    dialog = ui.ConfirmationDialog("dialog.shell",
                                                   "Begin Hosted Engine Setup",
                                                   txt
                                                   )
                    dialog.buttons[0].on_activate.clear()
                    dialog.buttons[0].on_activate.connect(ui.CloseAction())
                    dialog.buttons[0].on_activate.connect(return_ok)
                else:
                    if self._model['display_message']:
                        msg = self._model['display_message']
                        self._model['display_message'] = ''
                    else:
                        msg = "\n\nError Downloading ISO/OVA Image!"

                    dialog = ui.InfoDialog("dialog.notice",
                                           "Hosted Engine Setup",
                                           msg)

                self.application.show(dialog)

            except:
                # Error when the UI is not running
                self.logger.info("Exception on TUI!", exc_info=True)
                open_console()

    def _image_retrieve(self, imagepath, setup_dir):
        _downloader = DownloadThread(self, imagepath, setup_dir)
        _downloader.start()


class DownloadThread(threading.Thread):
    ui_thread = None

    def __init__(self, plugin, url, setup_dir):
        super(DownloadThread, self).__init__()
        self.he_plugin = plugin
        self.url = url
        self.setup_dir = setup_dir

    @property
    def logger(self):
        return self.he_plugin.logger

    def run(self):
        try:
            self.app = self.he_plugin.application
            self.ui_thread = self.app.ui.thread_connection()

            self.__run()
        except Exception as e:
            self.logger.exception("Downloader thread failed: %s " % e)

    def __run(self):
        # Wait a second before the UI refresh so we get the right widgets
        time.sleep(.5)

        path = "%s/%s" % (self.setup_dir, self.url.split('/')[-1])

        ui_is_alive = lambda: any((t.name == "MainThread") and t.is_alive() for
                                  t in threading.enumerate())

        with open(path, 'wb') as f:
            started = time.time()
            try:
                r = requests.get(self.url, stream=True)
                if r.status_code != 200:
                    self.he_plugin._model['display_message'] = \
                        "\n\nCannot download the file: HTTP error code %s" % \
                        str(r.status_code)
                    os.unlink(path)
                    return self.he_plugin.show_dialog()

                size = r.headers.get('content-length')
            except requests.exceptions.ConnectionError as e:
                self.logger.info("Error downloading: %s" % e[0], exc_info=True)
                self.he_plugin._model['display_message'] = \
                    "\n\nConnection Error: %s!" % str(e[0])
                os.unlink(path)
                return self.he_plugin.show_dialog()

            downloaded = 0

            def update_ui():
                # Get new handles every time, since switching pages means
                # the widgets will get rebuilt and we need new handles to
                # update
                progressbar = self.he_plugin.widgets["download.progress"]
                status = self.he_plugin.widgets["download.status"]

                current = int(100.0 * (float(downloaded) / float(size)))

                progressbar.current(current)
                speed = calculate_speed()
                status.text(speed)

                # Save it in the model so the page can update immediately
                # on switching back instead of waiting for a tick
                self.he_plugin._model.update({"download.status": speed})
                self.he_plugin._model.update({"download.progressbar": current})

            def calculate_speed():
                raw = downloaded // (time.time() - started)
                i = 0
                friendly_names = ("B", "KB", "MB", "GB")
                if int(raw / 1024) > 0:
                    raw = raw / 1024
                    i += 1
                return "%0.2f %s/s" % (raw, friendly_names[i])

            for chunk in r.iter_content(1024 * 256):
                downloaded += len(chunk)
                f.write(chunk)

                if ui_is_alive():
                    self.ui_thread.call(update_ui())
                else:
                    break

        if not ui_is_alive():
            os.unlink(path)

        else:
            self.he_plugin._configure_ISO_OVA = True
            self.he_plugin.show_dialog()


class HostedEngine(NodeConfigFileSection):
    keys = ("OVIRT_HOSTED_ENGINE_IMAGE_PATH",
            "OVIRT_HOSTED_ENGINE_PXE",
            )

    @NodeConfigFileSection.map_and_update_defaults_decorator
    def update(self, imagepath, pxe):
        if not isinstance(pxe, bool):
            pxe = True if pxe.lower() == 'true' else False
        (valid.Empty() | valid.Text())(imagepath)
        (valid.Boolean()(pxe))
        return {"OVIRT_HOSTED_ENGINE_IMAGE_PATH": imagepath,
                "OVIRT_HOSTED_ENGINE_PXE": "yes" if pxe else None}
