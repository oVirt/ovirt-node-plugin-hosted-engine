#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# hosted_engine_model.py - Copyright (C) 2015 Red Hat, Inc.
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

from ovirt.node import utils
from ovirt.node.config.defaults import NodeConfigFileSection
from ovirt.node.utils.fs import File
from ovirt.node import valid
from . import config
import os


class HostedEngine(NodeConfigFileSection):
    keys = ("OVIRT_HOSTED_ENGINE_IMAGE_PATH",
            "OVIRT_HOSTED_ENGINE_PXE",
            "OVIRT_HOSTED_ENGINE_FORCE_ENABLE",
            )

    @NodeConfigFileSection.map_and_update_defaults_decorator
    def update(self, imagepath, pxe, force_enable=None):
        if not isinstance(pxe, bool):
            pxe = True if pxe.lower() == 'true' else False
        (valid.Empty() | valid.Text())(imagepath)
        (valid.Boolean()(pxe))
        return {"OVIRT_HOSTED_ENGINE_IMAGE_PATH": imagepath,
                "OVIRT_HOSTED_ENGINE_PXE": "yes" if pxe else None,
                "OVIRT_HOSTED_ENGINE_FORCE_ENABLE": "yes" if force_enable
                else None}

    def retrieve(self):
        cfg = dict(NodeConfigFileSection.retrieve(self))
        cfg.update({"pxe": True if cfg["pxe"] == "yes" else False})
        cfg.update({"force_enable": True if cfg["force_enable"] == "yes"
                    else False})
        return cfg

    def transaction(self, temp_cfg_file):

        txs = utils.Transaction("Updating Hosted Engine related configuration")

        class WriteConfig(utils.Transaction.Element):
            title = "Writing Hosted Engine Config File"

            def commit(self):
                cfg = HostedEngine().retrieve()

                def magic_type(mtype="gzip"):
                    magic_headers = {"gzip": "\x1f\x8b\x08"}
                    magic = None

                    if not imagepath == "%s/" % config.HOSTED_ENGINE_SETUP_DIR:
                        with open(imagepath) as f:
                            magic = f.read(len(magic_headers[mtype]))

                    return True if magic == magic_headers[mtype] else False

                def write(line):
                    f.write("{line}\n".format(line=line), "a")

                f = File(temp_cfg_file)

                self.logger.info("Saving Hosted Engine Config")

                ova_path = None
                boot = None
                write("[environment:default]")

                if cfg["pxe"]:
                    boot = "pxe"

                if cfg["imagepath"]:
                    if "file://" in cfg["imagepath"]:
                        imagepath = cfg["imagepath"][7:]
                    else:
                        imagepath = os.path.join(
                            config.HOSTED_ENGINE_SETUP_DIR,
                            os.path.basename(cfg["imagepath"]).lstrip("/"))
                    if imagepath.endswith(".iso"):
                        boot = "cdrom"
                        write("OVEHOSTED_VM/vmCDRom=str:{imagepath}".format(
                            imagepath=imagepath))
                    else:
                        imagetype = "gzip" if magic_type() else "Unknown"

                        if imagetype == "gzip":
                            boot = "disk"
                            ova_path = imagepath
                        else:
                            os.unlink(temp_cfg_file)
                            raise RuntimeError("Downloaded image is neither an"
                                               " OVA nor an ISO, can't use it")

                bootstr = "str:{boot}".format(
                    boot=boot
                ) if boot else "none:None"
                write("OVEHOSTED_VM/vmBoot={bootstr}".format(bootstr=bootstr))

                ovastr = "str:{ova_path}".format(ova_path=ova_path) if \
                    ova_path else "none:None"
                write("OVEHOSTED_VM/ovfArchive={ovastr}".format(ovastr=ovastr))

                write("OVEHOSTED_CORE/tempDir=str:{tmpdirHE}".format(
                      tmpdirHE=config.HOSTED_ENGINE_TEMPDIR))

                self.logger.info("Wrote hosted engine install configuration to"
                                 " {cfg}".format(cfg=temp_cfg_file))
                self.logger.debug("Wrote config as:")
                for line in f:
                    self.logger.debug("{line}".format(line=line.strip()))

        txs.append(WriteConfig())
        return txs
