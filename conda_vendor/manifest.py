import hashlib
import json
import logging
import os
from pathlib import Path
import requests
import struct
import sys
import yaml
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import collections

from conda_lock.conda_lock import solve_specs_for_arch
from conda_lock.src_parser.environment_yaml import parse_environment_file


logging.basicConfig(level=logging.INFO)


class LockWrapper:
    @staticmethod
    def parse(*args):
        return parse_environment_file(*args)

    @staticmethod
    def solve(*args, **kwargs):
        return solve_specs_for_arch(*args, **kwargs)


# see https://github.com/conda/conda/blob/248741a843e8ce9283fa94e6e4ec9c2fafeb76fd/conda/base/context.py#L51
def get_conda_platform(platform=sys.platform):
    _platform_map = {
        "linux2": "linux",
        "linux": "linux",
        "darwin": "osx",
        "win32": "win",
        "zos": "zos",
    }

    bits = struct.calcsize("P") * 8
    return f"{_platform_map[platform]}-{bits}"


class MetaManifest:
    def __init__(self, environment_yml, *, manifest_root=Path()):
        self.manifest_root = Path(manifest_root)
        logging.info(f"manifest_root : {self.manifest_root.absolute()}")
        self.platform = get_conda_platform()

        self.valid_platforms = [self.platform, "noarch"]

        # create from envirenment yaml
        self.manifest = None
        parse_return = LockWrapper.parse(environment_yml, self.platform)
        print(f"{parse_return=}")
        self.env_deps = {
            "specs": parse_return.specs,
            "channels": parse_return.channels,
        }

        logging.info(f"Using Environment :{environment_yml}")
        with open(environment_yml) as f:
            self.env_deps["environment"] = yaml.load(f, Loader=yaml.SafeLoader)
        bad_channels = ["nodefaults"]
        self.channels = [
            chan
            for chan in self.env_deps["environment"]["channels"]
            if chan not in bad_channels
        ]
        if "defaults" in self.channels:
            raise RuntimeError("default channels are not supported.")

    def get_manifest_filename(self, manifest_filename=None):
        if manifest_filename is None:
            manifest_filename = "vendor_manifest.yaml"
        return manifest_filename

    def create_manifest(self, *, manifest_filename=None):
        # TODO: This will still create a manifest if creating from a manifest. Should probably be removed if creating from manifest.
        manifest = self.get_manifest()

        manifest_filename = self.get_manifest_filename(
            manifest_filename=manifest_filename
        )

        cleaned_name = Path(manifest_filename).name
        outpath_file_name = self.manifest_root / cleaned_name
        logging.info(f"Creating Manifest {outpath_file_name.absolute()}")
        with open(outpath_file_name, "w") as f:
            yaml.dump(manifest, f, sort_keys=False)
        return manifest

    def get_manifest(self):
        if self.manifest is None:

            def nested_dict():
                return collections.defaultdict(nested_dict)

            d = nested_dict()

            fetch_actions = self.solve_environment()

            for chan in self.channels:  # edit to self.channels
                d[chan]["noarch"] = {"repodata_url": [], "entries": []}
                d[chan][self.platform] = {"repodata_url": [], "entries": []}

            for entry in fetch_actions:
                print(entry)
                (channel, platform) = entry["channel"].split("/")[-2:]

                d[channel][platform][
                    "repodata_url"
                ] = f"{entry['channel']}/repodata.json"
                entry["purl"] = self.get_purl(entry)
                d[channel][platform]["entries"].append(entry)

            # Turns nested default dict into normal python dict
            self.manifest = json.loads(json.dumps(d))
        return self.manifest

    def get_purl(self, fetch_entry):
        """
        Returns package url format based on item in fetch data
        see: https://github.com/package-url/purl-spec 
        """
        return f"pkg:conda/{fetch_entry['name']}@{fetch_entry['version']}?url={fetch_entry['url']}"

    def solve_environment(self):
        if "solution" not in self.env_deps:
            logging.info(
                f"Solving ENV | Channels : {self.env_deps['channels']} | specs : {self.env_deps['specs']} , platform : {self.platform}"
            )
            solution = LockWrapper.solve(
                "conda",
                self.env_deps["channels"],
                specs=self.env_deps["specs"],
                platform=self.platform,
            )
            self.env_deps["solution"] = solution
        return self.env_deps["solution"]["actions"]["FETCH"]