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
from ovirt.node import plugins, ui, utils, valid
from ovirt.node.plugins import Changeset
from ovirt.node.utils import console
from ovirt.node.utils.fs import Config, File
from ovirt.node.utils.network import NodeNetwork
from ovirt_hosted_engine_ha.client import client
from . import config
from .hosted_engine_model import HostedEngine

import json
import os
import requests
import sys
import tempfile
import threading
import time

"""
Configure Hosted Engine
"""


class Plugin(plugins.NodePlugin):
    _server = None
    _show_progressbar = False
    _model = {}
    _install_ready = False

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

        conf_status = "Configured" if self._configured() else "Not configured"
        vm_status = self.__get_vm_status()
        vm = None

        if self._configured():
            vm = self._read_attr_config(config.VM_CONF_PATH, "fqdn")

        model = {
            "hosted_engine.enabled": str(conf_status),
            "hosted_engine.vm": vm,
            "hosted_engine.status": vm_status,
            "hosted_engine.diskpath": cfg["imagepath"] or "",
            "hosted_engine.display_message": "",
            "hosted_engine.pxe": cfg["pxe"]}

        self._model.update(model)

        return self._model

    def validators(self):
        return {"hosted_engine.diskpath": valid.Empty() | valid.URL()}

    def ui_content(self):
        # Update the status on a page refresh
        self._model["hosted_engine.status"] = self.__get_vm_status()

        network_up = NodeNetwork().is_configured()

        ws = [ui.Header("header[0]", "Hosted Engine Setup")]

        if network_up:
            ws.extend([ui.KeywordLabel("hosted_engine.enabled",
                                       ("Hosted Engine: "))])
        else:
            ws.extend([ui.Notice("network.notice", "Networking is not " +
                                 "configured please configure it before " +
                                 "setting up hosted engine")])

        if self._configured():
            ws.extend([ui.Divider("divider[0]"),
                       ui.KeywordLabel("hosted_engine.vm",
                                       ("Engine VM: ")),
                       ui.KeywordLabel("hosted_engine.status",
                                       ("Engine Status: ")),
                       ui.Button("button.status", "Hosted Engine VM status"),
                       ui.Button("button.maintenance",
                                 "Set Hosted Engine maintenance")])

        if network_up:
            ws.extend([ui.Divider("divider.button"),
                       ui.Button("button.dialog", "Deploy Hosted Engine")])

        if self._show_progressbar:
            if "progress" in self._model:
                ws.append(ui.ProgressBar("download.progress",
                                         int(self._model["progress"])))
            else:
                ws.append(ui.ProgressBar("download.progress", 0))

            ws.append(ui.KeywordLabel("download.status", ""))

        page = ui.Page("page", ws)
        self.widgets.add(page)
        return page

    def on_change(self, changes):
        pass

    def on_merge(self, effective_changes):
        def close_dialog():
            if self._dialog:
                self._dialog.close()
                self._dialog = None
        self._install_ready = False
        self._invalid_download = False
        self.temp_cfg_file = False

        effective_changes = Changeset(effective_changes)
        changes = Changeset(self.pending_changes(False))
        effective_model = Changeset(self.model())
        effective_model.update(effective_changes)

        self.logger.debug("Changes: %s" % changes)
        self.logger.debug("Effective Model: %s" % effective_model)

        if "button.dialog" in effective_changes:
            self._dialog = DeployDialog("Deploy Hosted Engine", self)
            self.widgets.add(self._dialog)
            return self._dialog

        if "button.status" in effective_changes:
            try:
                contents = utils.process.check_output(
                    ["hosted-engine",
                     "--vm-status"],
                    stderr=utils.process.STDOUT
                )
            except utils.process.CalledProcessError:
                contents = "\nFailed to collect hosted engine vm status, " \
                           "check ovirt-ha-broker logs."

            return ui.TextViewDialog("output.dialog", "Hosted Engine VM "
                                     "Status", contents)

        if "button.maintenance" in effective_changes:
            self._dialog = MaintenanceDialog("Hosted Engine Maintenance", self)
            self.widgets.add(self._dialog)
            return self._dialog

        if "maintenance.confirm" in effective_changes:
            close_dialog()
            if "maintenance.level" in effective_changes:
                level = effective_changes["maintenance.level"]
                try:
                    utils.process.check_call(["hosted-engine",
                                              "--set-maintenance",
                                              "--mode=%s" % level])
                except:
                    self.logger.exception("Couldn't set maintenance level "
                                          "to %s" % level, exc_info=True)
                    return ui.InfoDialog("dialog.error", "An error occurred",
                                         "Couldn't set maintenance level to "
                                         "%s. Check the logs" % level)

        if "deploy.additional" in effective_changes:
            close_dialog()

            def run_additional(*args):
                with self.application.ui.suspended():
                    try:
                        utils.process.call(
                            "reset; screen hosted-engine --deploy",
                            shell=True)
                        sys.stdout.write("Press <Return> to return to the TUI")
                        console.wait_for_keypress()
                        self.__persist_configs()
                    except:
                        self.logger.exception("hosted-engine failed to "
                                              "deploy!", exc_info=True)

            txt = ("Please set a password on the RHEV-M page of a host "
                   "which has previously deployed hosted engine before "
                   "continuing. This is required to retrieve the setup "
                   "answer file")
            dialog = ui.ConfirmationDialog("dialog.add",
                                           "Prepare remote host", txt)

            yes_btn, cncl_btn = dialog.buttons
            yes_btn.label("Proceed")
            yes_btn.on_activate.connect(run_additional)
            return dialog
        if effective_changes.contains_any(["deploy.confirm"]):
            close_dialog()

            def make_tempfile():
                if not os.path.exists(config.HOSTED_ENGINE_SETUP_DIR):
                    os.makedirs(config.HOSTED_ENGINE_SETUP_DIR)

                if not os.path.exists(config.HOSTED_ENGINE_TEMPDIR):
                    os.makedirs(config.HOSTED_ENGINE_TEMPDIR)

                temp_fd, temp_cfg_file = tempfile.mkstemp()
                os.close(temp_fd)
                return temp_cfg_file

            imagepath = effective_model["hosted_engine.diskpath"]
            pxe = effective_model["hosted_engine.pxe"]
            localpath = None

            # FIXME: dynamically enable the fields so we can't get into
            # this kind of situation. Selection should be ui.Options, not
            # a checkbox and a blank entry field
            #
            # Check whether we have unclear conditions
            if not imagepath and not pxe:
                self._model['display_message'] = "\n\nYou must enter a URL" \
                    " or choose PXE to install the Engine VM"
                return self.show_dialog()
            elif imagepath and pxe:
                self._model['display_message'] = "\n\nPlease choose either " \
                                                 "PXE or an image to " \
                                                 "retrieve, not both"
                return self.show_dialog()

            self.temp_cfg_file = make_tempfile()

            engine_keys = ["hosted_engine.diskpath", "hosted_engine.pxe"]

            txs = utils.Transaction("Setting up hosted engine")

            # FIXME: The "None" is for force_enable
            # Why are we setting force_enable? It clutters the code. We should
            # move force enabling it to checking for --dry instead
            model = HostedEngine()
            args = tuple(effective_model.values_for(engine_keys)) + (None,)
            model.update(*args)

            if imagepath:
                localpath = os.path.join(config.HOSTED_ENGINE_SETUP_DIR,
                                         os.path.basename(imagepath))

            # Check whether we have enough conditions to run it right now
            if pxe or os.path.exists(localpath):
                def console_wait(event):
                    event.wait()
                    self._install_ready = True
                    self.show_dialog()

                txs += model.transaction(self.temp_cfg_file)
                progress_dialog = ui.TransactionProgressDialog("dialog.txs",
                                                               txs, self)

                t = threading.Thread(target=console_wait,
                                     args=(progress_dialog.event,))
                t.start()

                progress_dialog.run()

                # The application doesn't wait until the progressdialog is
                # done, and it ends up being called asynchronously. Calling
                # in a thread and waiting to set threading.Event
                # time.sleep(5)

            # Otherwise start an async download
            else:
                path_parsed = urlparse(imagepath)

                if not path_parsed.scheme:
                    self._model['display_message'] = ("\nCouldn't parse "
                                                      "URL. please check "
                                                      "it manually.")

                elif path_parsed.scheme == 'http' or \
                        path_parsed.scheme == 'https':
                    self._show_progressbar = True
                    self.application.show(self.ui_content())
                    self._image_retrieve(imagepath,
                                         config.HOSTED_ENGINE_SETUP_DIR)

        return self.ui_content()

    def show_dialog(self):
        def open_console():
            if self.temp_cfg_file and os.path.isfile(self.temp_cfg_file):
                try:
                    utils.process.call("reset; screen " +
                                       "ovirt-node-hosted-engine-setup" +
                                       " --config-append=%s" %
                                       self.temp_cfg_file, shell=True)
                    self.__persist_configs()
                except:
                    self.logger.exception("hosted-engine failed to deploy!",
                                          exc_info=True)

            else:
                self.logger.error("Cannot trigger ovirt-hosted-engine-setup" +
                                  " because the configuration file was not " +
                                  "generated, please check the location " +
                                  "referenced in /var/log/ovirt-node.log")

        def return_ok(dialog, changes):
            self.application.ui.close_dialog("Begin Hosted Engine Setup")
            with self.application.ui.suspended():
                open_console()

        if self.application.current_plugin() is self:
            try:
                # if show_progressbar is not set, the download process has
                # never started (PXE) or it finished
                if self._show_progressbar:
                    # Clear out the counters once we're done, and hide the
                    # progress bar
                    self.widgets["download.progress"].current(0)
                    self.widgets["download.status"].text("")
                    self._show_progressbar = False

                    self._model["download.progress"] = 0
                    self._model["download.status"] = ""

                # if the temp config file is empty, we removed it in the model
                # because there was an exception, so don't do anything
                if self.temp_cfg_file and not os.path.isfile(
                        self.temp_cfg_file):
                    self.logger.debug("The temporary config file %s does not"
                                      "exist. Not running screen")
                    return

                if self._install_ready:
                    utils.console.writeln("Beginning Hosted Engine Setup ...")
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
                    self.application.show(dialog)
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

        self.application.show(self.ui_content())

    def _read_attr_config(self, config_file, attr):
        """
        Read Attribute value from a config file

        config_file -- A .conf file
        attr -- The attribute for reading the value assigned

        Returns
        The value for attribute or None (No attribute found)
        """
        value = None

        f = File(config_file)
        if attr in f.read():
            value = [line.strip().split("=")[1] for line in f
                     if attr in line][0]

        return value

    def _configured(self):
        """
        Check if Hosted Engine is configured checking if
        hosted-engine.conf exists and contains vm_disk_id
        with a value.

        Return True or False
        """
        return bool(os.path.exists(config.VM_CONF_PATH) and
                    self._read_attr_config(config.VM_CONF_PATH, "vm_disk_id"))

    def __persist_configs(self):
        dirs = ["/etc/ovirt-hosted-engine", "/etc/ovirt-hosted-engine-ha",
                "/etc/ovirt-hosted-engine-setup.env.d"]
        [Config().persist(d) for d in dirs]

    def _image_retrieve(self, imagepath, setup_dir):
        _downloader = DownloadThread(self, imagepath, setup_dir)
        _downloader.start()

    def __get_ha_status(self):
        def dict_from_string(string):
            return json.loads(string)

        host = None

        ha_cli = client.HAClient()
        try:
            vm_status = ha_cli.get_all_host_stats()
        except:
            vm_status = "Cannot connect to HA daemon, please check the logs"
            return vm_status
        else:
            for v in vm_status.values():
                if dict_from_string(v['engine-status'])['health'] == "good":
                    host = "Here" if v['host-id'] == \
                           ha_cli.get_local_host_id() else v['hostname']
                    host = v['hostname']

        if not host:
            vm_status = "Engine is down or not deployed."
        else:
            vm_status = "Engine is running on {host}".format(host=host)

        return vm_status

    def __get_vm_status(self):
        if self._configured():
            return self.__get_ha_status()
        else:
            return "Hosted engine not configured"


class DeployDialog(ui.Dialog):
    """A dialog to input deployment information
    """
    def __init__(self, title, plugin):
        self.keys = ["hosted_engine.diskpath", "hosted_engine.pxe"]

        def clear_invalid(dialog, changes):
            [plugin.stash_change(prefix) for prefix in self.keys]

        entries = [ui.Entry("hosted_engine.diskpath",
                            "Engine ISO/OVA URL for download:"),
                   ui.Checkbox("hosted_engine.pxe", "PXE Boot Engine VM"),
                   ui.Divider("divider[1]"),
                   ui.SaveButton("deploy.additional",
                                 "Add this host to an existing group")]
        children = [ui.Label("label[0]", "Please provide details for "
                             "deployment of hosted engine"),
                    ui.Divider("divider[0]")]
        children.extend(entries)
        super(DeployDialog, self).__init__("deploy.dialog", title, children)
        self.buttons = [ui.SaveButton("deploy.confirm", "Deploy"),
                        ui.CloseButton("deploy.close", "Cancel")]

        b = plugins.UIElements(self.buttons)
        b["deploy.close"].on_activate.clear()
        b["deploy.close"].on_activate.connect(ui.CloseAction())
        b["deploy.close"].on_activate.connect(clear_invalid)


class MaintenanceDialog(ui.Dialog):
    """A dialog to set HE maintenance level
    """

    states = [("global", "Global"),
              ("local", "Local"),
              ("none", "None")]

    def __init__(self, title, plugin):
        self.keys = ["maintenance.level"]
        self.plugin = plugin

        def clear_invalid(dialog, changes):
            [plugin.stash_change(prefix) for prefix in self.keys]

        entries = [ui.Options("maintenance.level", "Maintenance Level",
                              self.states, selected=self.__vm_status())]
        children = [ui.Divider("divider.options"),
                    ui.Label("label[0]", "Please select the maintenance "
                             "level"),
                    ui.Divider("divider[0]")]
        children.extend(entries)
        super(MaintenanceDialog, self).__init__(
            "maintenance.dialog", title, children)
        self.buttons = [ui.SaveButton("maintenance.confirm", "Set"),
                        ui.CloseButton("maintenance.close", "Cancel")]

        b = plugins.UIElements(self.buttons)
        b["maintenance.close"].on_activate.clear()
        b["maintenance.close"].on_activate.connect(ui.CloseAction())
        b["maintenance.close"].on_activate.connect(clear_invalid)

    def __vm_status(self):
        ha_cli = client.HAClient()
        level = None

        try:
            level = "global" if ha_cli.get_all_stats(
                client.HAClient.StatModes.GLOBAL)[0]["maintenance"] else None
        except KeyError:
            # Stats returned but no global section
            pass

        if level is None:
            try:
                level = "local" if ha_cli.get_all_stats()[1]["maintenance"] \
                        else "none"
            except:
                self.plugin.logger.debug("Couldn't get HA stats!",
                                         exc_info=True)

        return level


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
                s = requests.Session()

                # Don't let apache transparently deflate gzips
                del s.headers["Accept-Encoding"]

                r = s.get(self.url, stream=True)
                if r.status_code != 200:
                    self.he_plugin._model['display_message'] = \
                        "\n\nCannot download the file: HTTP error code %s" % \
                        str(r.status_code)
                    os.unlink(path)
                    return self.he_plugin.show_dialog()

                size = r.headers.get('content-length')

                # Size isn't specified if it's chunked
                encoding = r.headers.get('transfer-encoding') if not size \
                    else None
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

                if encoding == 'chunked':
                    current = 0
                elif size:
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

            chunk = None
            while chunk != '':
                chunk = r.raw.read(1024 * 256)
                downloaded += len(chunk)
                f.write(chunk)

                if ui_is_alive():
                    self.ui_thread.call(update_ui())
                else:
                    break

        if not ui_is_alive():
            # If they've exited, clear out the file
            os.unlink(path)

        else:
            self.he_plugin.on_merge({"hosted_engine.diskpath": self.url,
                                     "deploy.confirm": True})
            self.he_plugin._install_ready = True
