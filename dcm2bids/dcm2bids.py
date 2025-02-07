# -*- coding: utf-8 -*-

"""
Reorganising NIfTI files from dcm2niix into the Brain Imaging Data Structure
"""

import argparse
import logging
import os
from pathlib import Path
import platform
import sys
from datetime import datetime
from glob import glob
import shutil # maureen added to copy instead of move files

from dcm2bids.dcm2niix import Dcm2niix
from dcm2bids.logger import setup_logging
from dcm2bids.sidecar import Sidecar, SidecarPairing
from dcm2bids.structure import Participant
from dcm2bids.utils import (DEFAULT, load_json, save_json,
                            splitext_, run_shell_command, valid_path)
from dcm2bids.version import __version__, check_latest, dcm2niix_version


class Dcm2bids(object):
    """ Object to handle dcm2bids execution steps

    Args:
        nii_dir (str): A list of folder with niis to convert
        participant (str): Label of your participant
        config (path): Path to a dcm2bids configuration file
        output_dir (path): Path to the BIDS base folder
        session (str): Optional label of a session
        clobber (boolean): Overwrite file if already in BIDS folder
        log_level (str): logging level
    """

    def __init__(
        self,
        nii_dir,
        participant,
        config,
        output_dir=DEFAULT.outputDir,
        session=DEFAULT.session,
        clobber=DEFAULT.clobber,
        log_level=DEFAULT.logLevel,
        **_
    ):
		
        self.niiDir = valid_path(nii_dir, type="folder")
        self.bidsDir = valid_path(output_dir, type="folder")
        self.config = load_json(valid_path(config, type="file"))
        self.participant = Participant(participant, session)
        self.clobber = clobber
        self.logLevel = log_level


        # logging setup
        self.set_logger()

        self.logger.info("--- dcm2bids start ---")
        self.logger.info("OS:version: %s", platform.platform())
        self.logger.info("python:version: %s", sys.version.replace("\n", ""))
        self.logger.info("dcm2bids:version: %s", __version__)
        self.logger.info("dcm2niix:version: %s", dcm2niix_version())
        self.logger.info("participant: %s", self.participant.name)
        self.logger.info("session: %s", self.participant.session)
        self.logger.info("config: %s", os.path.realpath(config))
        self.logger.info("BIDS directory: %s", os.path.realpath(output_dir))


    def set_logger(self):
        """ Set a basic logger"""
        logDir = self.bidsDir / DEFAULT.tmpDirName / "log"
        logFile = logDir / f"{self.participant.prefix}_{datetime.now().isoformat().replace(':', '')}.log"
        logDir.mkdir(parents=True, exist_ok=True)

        setup_logging(self.logLevel, logFile)
        self.logger = logging.getLogger(__name__)

    def run(self):
        """Run dcm2bids"""
        sidecarFiles = Path(str(self.niiDir)).rglob('*.json')


        sidecars = []
        for filename in sidecarFiles:
            print(filename)
            sidecars.append(
                Sidecar(str(filename), self.config.get("compKeys", DEFAULT.compKeys))
            )

        sidecars = sorted(sidecars)

        parser = SidecarPairing(
            sidecars,
            self.config["descriptions"],
            self.config.get("searchMethod", DEFAULT.searchMethod),
            self.config.get("caseSensitive", DEFAULT.caseSensitive)
        )
        parser.build_graph()
        parser.build_acquisitions(self.participant)
        parser.find_runs()

        self.logger.info("moving acquisitions into BIDS folder")

        intendedForList = [[] for i in range(len(parser.descriptions))]
        for acq in parser.acquisitions:
            acq.setDstFile()
            intendedForList = self.move(acq, intendedForList)

    def move(self, acquisition, intendedForList):
        """Move an acquisition to BIDS format"""
        for srcFile in glob(acquisition.srcRoot + ".*"):

            ext = Path(srcFile).suffixes
            ext = [curr_ext for curr_ext in ext if curr_ext in ['.nii','.gz',
                                                                '.json',
                                                                '.bval','.bvec']]

            dstFile = (self.bidsDir / acquisition.dstRoot).with_suffix("".join(ext))

            dstFile.parent.mkdir(parents = True, exist_ok = True)

            # checking if destination file exists
            if dstFile.exists():
                self.logger.info("'%s' already exists", dstFile)

                if self.clobber:
                    self.logger.info("Overwriting because of --clobber option")

                else:
                    self.logger.info("Use --clobber option to overwrite")
                    continue

            # it's an anat nifti file and the user using a deface script
            if (
                self.config.get("defaceTpl")
                and acquisition.dataType == "func"
                and ".nii" in ext
                ):
                try:
                    os.remove(dstFile)
                except FileNotFoundError:
                    pass
                defaceTpl = self.config.get("defaceTpl")

                cmd = [w.replace('srcFile', srcFile) for w in defaceTpl]
                cmd = [w.replace('dstFile', dstFile) for w in defaceTpl]
                run_shell_command(cmd)

                intendedForList[acquisition.indexSidecar].append(acquisition.dstIntendedFor + "".join(ext))

            elif ".json" in ext:
                data = acquisition.dstSidecarData(self.config["descriptions"],
                                                  intendedForList)
                save_json(dstFile, data)
#                 os.remove(srcFile)

            # just move
            else:
#                 os.rename(srcFile, dstFile)
                shutil.copyfile(srcFile, dstFile)

            intendedFile = acquisition.dstIntendedFor + ".nii.gz"
            if intendedFile not in intendedForList[acquisition.indexSidecar]:
                intendedForList[acquisition.indexSidecar].append(intendedFile)

        return intendedForList


def _build_arg_parser():
    p = argparse.ArgumentParser(description=__doc__, epilog=DEFAULT.EPILOG,
                                formatter_class=argparse.RawTextHelpFormatter)

    p.add_argument("-d", "--nii_dir",
                   type=Path, 
                   required=True, 
                   help="DICOM directory(ies).")

    p.add_argument("-p", "--participant",
                   required=True,
                   help="Participant ID.")

    p.add_argument("-s", "--session",
                   required=False,
                   default="",
                   help="Session ID.")

    p.add_argument("-c", "--config",
                   type=Path,
                   required=True,
                   help="JSON configuration file (see example/config.json).")

    p.add_argument("-o", "--output_dir",
                   required=False,
                   type=Path,
                   default=Path.cwd(),
                   help="Output BIDS directory. (Default: %(default)s)")

    p.add_argument("--clobber",
                   action="store_true",
                   help="Overwrite output if it exists.")

    p.add_argument("-l", "--log_level",
                   required=False,
                   default=DEFAULT.cliLogLevel,
                   choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                   help="Set logging level. [%(default)s]")

    return p


def main():
    """Let's go"""
    parser = _build_arg_parser()
    args = parser.parse_args()

    app = Dcm2bids(**vars(args))
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
